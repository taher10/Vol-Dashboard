"""
src/dashboard/strategy_engine.py

Strategy Builder — turns a stated view (direction + timeline + risk
appetite + capital) into exactly ONE concrete, risk-defined vertical spread
(Bull Call, Bull Put, Bear Put, or Bear Call), sized to the trader's stated
capital. Pure pandas/numpy, no I/O — same house style as decision_engine.py.

Deliberately recommends one trade, not a ranked list: risk appetite fully
determines the structure (debit vs. credit), target delta, and wing width up
front (see RISK_PROFILES below), so there's nothing left to rank between
different structures for the same view -- only which expiry (within the
stated timeline) is best, which recommend_trade() still resolves via
rank_candidates().

Design note: rather than hand-deriving max profit/loss/breakeven formulas
per structure (easy to get subtly wrong and to have drift out of sync across
multiple structures), every structure is reduced to a list of Legs and a
single `_summarize()` samples the piecewise-linear payoff at expiration
directly from each leg's actual strike/action/mid price. Max profit/loss and
breakevens are then read off that one payoff curve — one code path, always
consistent with what's plotted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

Direction = Literal["bullish", "bearish"]
Timeline = Literal["short", "medium", "long"]
RiskAppetite = Literal["conservative", "moderate", "aggressive"]
Structure = Literal["debit", "credit"]
OptionSide = Literal["CALL", "PUT"]
Action = Literal["buy", "sell"]

_STRUCTURE_LABELS: dict[tuple[str, str], str] = {
    ("bullish", "debit"): "Bull Call Spread",
    ("bullish", "credit"): "Bull Put Spread",
    ("bearish", "debit"): "Bear Put Spread",
    ("bearish", "credit"): "Bear Call Spread",
}


@dataclass
class Leg:
    action: Action
    optionType: OptionSide
    strike: float
    delta: float | None
    mid: float


@dataclass
class Candidate:
    structure: str
    direction: str
    expiration: pd.Timestamp
    dte: int
    legs: list[Leg]
    net_debit_credit: float
    max_profit: float
    max_loss: float
    breakevens: list[float]
    approx_pop: float
    payoff: list[dict] = field(default_factory=list)


def _mid(row: pd.Series) -> float:
    return float((row["bid"] + row["ask"]) / 2.0)


def _nearest_to_delta(chain_slice: pd.DataFrame, option_type: OptionSide, target_delta: float) -> pd.Series | None:
    """The listed contract of `option_type` whose |delta| is closest to target_delta."""
    side = chain_slice[chain_slice["optionType"] == option_type].dropna(subset=["delta", "bid", "ask"])
    if side.empty:
        return None
    side = side.copy()
    side["_distance"] = (side["delta"].abs() - target_delta).abs()
    return side.sort_values("_distance").iloc[0]


def _strike_neighbor(
    chain_slice: pd.DataFrame,
    option_type: OptionSide,
    from_strike: float,
    steps: int,
    direction_sign: int,
) -> pd.Series | None:
    """
    The listed strike `steps` increments away from from_strike, in
    direction_sign (+1 = next higher strikes, -1 = next lower). Snaps
    from_strike to the nearest real strike first in case it isn't an exact
    match (shouldn't normally happen, since from_strike always comes from a
    real row, but guards against float drift).

    Requires a non-null delta too (matching _nearest_to_delta), not just
    strikePrice/bid/ask: every call site currently pre-filters its chain_slice
    for non-null delta already, so this is belt-and-suspenders rather than a
    live bug fix, but it keeps this shared helper self-sufficient instead of
    silently relying on callers to have pre-filtered.
    """
    side = chain_slice[chain_slice["optionType"] == option_type].dropna(subset=["strikePrice", "bid", "ask", "delta"])
    if side.empty:
        return None
    strikes = sorted(side["strikePrice"].unique())
    nearest = min(strikes, key=lambda s: abs(s - from_strike))
    idx = strikes.index(nearest) + direction_sign * steps
    if idx < 0 or idx >= len(strikes):
        return None
    target_strike = strikes[idx]
    return side[side["strikePrice"] == target_strike].iloc[0]


def _summarize(legs: list[Leg], n_points: int = 121, pad_pct: float = 0.08) -> dict:
    """Sample the piecewise-linear expiration payoff across a price range that
    comfortably brackets every leg's strike, then read max profit/loss (the
    flat asymptotes beyond the outer strikes) and breakeven(s) (zero
    crossings, linearly interpolated) directly off that curve."""
    strikes = [leg.strike for leg in legs]
    lo, hi = min(strikes) * (1 - pad_pct), max(strikes) * (1 + pad_pct)
    if hi <= lo:
        lo, hi = lo * 0.9, hi * 1.1

    prices = np.linspace(lo, hi, n_points)
    pnl = np.zeros(n_points)
    for leg in legs:
        intrinsic = (
            np.maximum(prices - leg.strike, 0.0)
            if leg.optionType == "CALL"
            else np.maximum(leg.strike - prices, 0.0)
        )
        pnl += (intrinsic - leg.mid) if leg.action == "buy" else (leg.mid - intrinsic)

    net_debit_credit = float(sum(leg.mid if leg.action == "buy" else -leg.mid for leg in legs))

    breakevens: list[float] = []
    for i in range(len(prices) - 1):
        p0, p1 = pnl[i], pnl[i + 1]
        if p0 == 0:
            breakevens.append(float(prices[i]))
        elif (p0 < 0 < p1) or (p1 < 0 < p0):
            frac = -p0 / (p1 - p0)
            breakevens.append(float(prices[i] + frac * (prices[i + 1] - prices[i])))

    return {
        "net_debit_credit": net_debit_credit,
        "max_profit": float(max(0.0, pnl.max())),
        "max_loss": float(max(0.0, -pnl.min())),
        "breakevens": breakevens,
        "payoff": [{"underlying": float(p), "pnl": float(v)} for p, v in zip(prices, pnl)],
    }


def _delta_at_price(exp_chain: pd.DataFrame, option_type: OptionSide, price: float) -> float | None:
    """|delta| interpolated (vs. strike) at an arbitrary price, for prices that
    fall between listed strikes -- e.g. a breakeven, which is rarely itself a
    listed strike. Clamped at the ends rather than extrapolated."""
    side = exp_chain[exp_chain["optionType"] == option_type].dropna(subset=["delta", "strikePrice"])
    if side.empty:
        return None
    side = side.sort_values("strikePrice")
    strikes = side["strikePrice"].to_numpy()
    deltas = side["delta"].abs().to_numpy()
    if price <= strikes[0]:
        return float(deltas[0])
    if price >= strikes[-1]:
        return float(deltas[-1])
    return float(np.interp(price, strikes, deltas))


def _approx_pop(exp_chain: pd.DataFrame, legs: list[Leg], breakevens: list[float], is_credit: bool) -> float:
    """
    Delta-as-probability-proxy -- the standard retail-trader rough heuristic
    (NOT a priced probability model). For a debit structure, the bought
    (closer-to-money) leg's own |delta| approximates the chance it finishes
    ITM at all.

    For a credit structure, POP is evaluated at the actual breakeven price(s)
    rather than at the short strike(s) directly: using the short strike's own
    delta works fine for an OTM vertical/Iron Condor (the breakeven sits only
    slightly past it), but breaks down for Iron Butterfly, where the short
    strikes are ATM by construction -- |put delta| + |call delta| there is
    always ~1.0 regardless of how wide the actual (credit-funded) profit zone
    is, which would read as "~0% chance of profit" on every single Iron
    Butterfly. Evaluating delta at the breakeven itself (interpolated across
    the chain's own delta-vs-strike curve) reflects the real profit zone
    width and gives a sane, comparable number across every structure.
    """
    if not is_credit:
        long_deltas = [abs(leg.delta) for leg in legs if leg.action == "buy" and leg.delta is not None]
        return max(long_deltas) if long_deltas else 0.0

    if not breakevens:
        return 0.0

    if len(breakevens) == 1:
        side = legs[0].optionType
        d = _delta_at_price(exp_chain, side, breakevens[0])
        return max(0.0, 1.0 - d) if d is not None else 0.0

    lower, upper = min(breakevens), max(breakevens)
    d_put = _delta_at_price(exp_chain, "PUT", lower) or 0.0
    d_call = _delta_at_price(exp_chain, "CALL", upper) or 0.0
    return max(0.0, 1.0 - d_put - d_call)


def build_vertical(
    chain: pd.DataFrame,
    expiration: pd.Timestamp,
    direction: Literal["bullish", "bearish"],
    structure: Structure,
    target_short_delta: float = 0.30,
    width_strikes: int = 2,
) -> Candidate | None:
    """
    Build one vertical spread for `expiration`. `target_short_delta` locates
    the "primary" leg (sold for a credit spread, bought for a debit spread);
    `width_strikes` locates the other leg that many listed strikes further
    OTM on the same side. Returns None if the chain can't support either leg
    (e.g. not enough strikes listed that far out).
    """
    exp_chain = chain[chain["expiration"] == expiration].dropna(subset=["delta", "bid", "ask", "strikePrice"])
    if exp_chain.empty:
        return None

    option_type: OptionSide = "PUT" if (direction, structure) in {("bullish", "credit"), ("bearish", "debit")} else "CALL"
    width_sign = -1 if option_type == "PUT" else 1  # more-OTM strikes: lower for puts, higher for calls

    primary = _nearest_to_delta(exp_chain, option_type, target_short_delta)
    if primary is None:
        return None
    width_row = _strike_neighbor(exp_chain, option_type, float(primary["strikePrice"]), width_strikes, width_sign)
    if width_row is None:
        return None

    primary_action: Action = "sell" if structure == "credit" else "buy"
    width_action: Action = "buy" if structure == "credit" else "sell"

    legs = [
        Leg(primary_action, option_type, float(primary["strikePrice"]), float(primary["delta"]), _mid(primary)),
        Leg(width_action, option_type, float(width_row["strikePrice"]), float(width_row["delta"]), _mid(width_row)),
    ]
    summary = _summarize(legs)
    return Candidate(
        structure=_STRUCTURE_LABELS[(direction, structure)],
        direction=direction,
        expiration=expiration,
        dte=int(exp_chain["dte"].iloc[0]),
        legs=legs,
        approx_pop=_approx_pop(exp_chain, legs, summary["breakevens"], is_credit=(structure == "credit")),
        **summary,
    )


def rank_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """Reward-per-unit-risk first (max_profit / max_loss, descending), ties broken by approx_pop descending."""

    def key(c: Candidate) -> tuple[float, float]:
        ratio = c.max_profit / c.max_loss if c.max_loss > 0 else float("inf")
        return (-ratio, -c.approx_pop)

    return sorted(candidates, key=key)


# ----------------------------------------------------------------------
# Single-trade recommendation
# ----------------------------------------------------------------------

# DTE window per stated timeline -- kept inside the 7-90 window the pipeline
# actually fetches weekly-granularity data for (see options_fetcher.py).
TIMELINE_DTE_RANGES: dict[Timeline, tuple[int, int]] = {
    "short": (7, 21),
    "medium": (21, 45),
    "long": (45, 90),
}


@dataclass(frozen=True)
class RiskProfile:
    structure: Structure
    target_short_delta: float
    width_strikes: int


# Risk appetite fully determines structure + delta + width up front, so
# there's exactly one structure per (direction, risk) combination to build
# per expiry -- nothing left to compare debit-vs-credit or wide-vs-narrow on.
# Conservative -> credit spread, further OTM, wider wing: higher POP, smaller
# reward, the "collect premium" income-style approach. Aggressive -> debit
# spread, closer to the money, narrower wing: lower POP, larger reward
# multiple, a real directional-conviction play. Moderate sits in between.
RISK_PROFILES: dict[RiskAppetite, RiskProfile] = {
    "conservative": RiskProfile(structure="credit", target_short_delta=0.20, width_strikes=3),
    "moderate": RiskProfile(structure="credit", target_short_delta=0.30, width_strikes=2),
    "aggressive": RiskProfile(structure="debit", target_short_delta=0.40, width_strikes=1),
}


@dataclass
class PositionSizing:
    capital_available: float
    max_loss_per_contract: float
    contracts: int
    capital_used: float
    capital_used_pct: float
    total_max_profit: float
    total_max_loss: float


@dataclass
class Recommendation:
    candidate: Candidate
    sizing: PositionSizing | None


def _size_position(candidate: Candidate, capital: float) -> PositionSizing | None:
    """
    How many contracts `capital` affords, using the standard 100-share
    multiplier on the per-share max_loss already computed by _summarize().
    None if capital doesn't cover even one contract (candidate.max_loss == 0
    can't happen for a real spread with a nonzero width, but guarded anyway).
    """
    max_loss_per_contract = candidate.max_loss * 100
    if max_loss_per_contract <= 0:
        return None
    contracts = int(capital // max_loss_per_contract)
    if contracts < 1:
        return None
    capital_used = contracts * max_loss_per_contract
    return PositionSizing(
        capital_available=capital,
        max_loss_per_contract=max_loss_per_contract,
        contracts=contracts,
        capital_used=capital_used,
        capital_used_pct=(capital_used / capital * 100) if capital > 0 else 0.0,
        total_max_profit=contracts * candidate.max_profit * 100,
        total_max_loss=contracts * candidate.max_loss * 100,
    )


def recommend_trade(
    chain: pd.DataFrame,
    direction: Direction,
    timeline: Timeline,
    risk: RiskAppetite,
    capital: float | None = None,
) -> Recommendation | None:
    """
    The one best vertical spread for a stated (direction, timeline, risk)
    view: risk appetite fixes the structure/delta/width (RISK_PROFILES),
    timeline fixes the DTE window (TIMELINE_DTE_RANGES); the only thing left
    to choose is which expiry in that window gives the best reward-per-risk,
    via the same rank_candidates() used previously across whole candidate
    lists. Returns None if no expiry in the window can support this
    structure (e.g. not enough strikes listed that far OTM).

    `capital`, if given, sizes the position (see _size_position) -- omitted
    (None) if capital isn't provided or doesn't cover even one contract, so
    callers can distinguish "here's the trade, no sizing requested/possible"
    from a hard failure.
    """
    if chain is None or chain.empty or "dte" not in chain.columns:
        return None

    profile = RISK_PROFILES[risk]
    dte_min, dte_max = TIMELINE_DTE_RANGES[timeline]
    in_window = chain[(chain["dte"] >= dte_min) & (chain["dte"] <= dte_max)]
    expirations = sorted(in_window["expiration"].dropna().unique())

    candidates: list[Candidate] = []
    for expiration in expirations:
        cand = build_vertical(
            chain, expiration, direction, profile.structure, profile.target_short_delta, profile.width_strikes
        )
        if cand is not None:
            candidates.append(cand)

    if not candidates:
        return None

    best = rank_candidates(candidates)[0]
    sizing = _size_position(best, capital) if capital is not None and capital > 0 else None
    return Recommendation(candidate=best, sizing=sizing)
