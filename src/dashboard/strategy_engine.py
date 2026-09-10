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
    # Only set for multi-expiration structures (currently just the calendar
    # spread) -- every other structure's legs all share their Candidate's own
    # single `expiration`, so this stays None for them and every existing
    # call site is unaffected.
    expiration: pd.Timestamp | None = None
    implied_volatility: float | None = None
    vega: float | None = None


CONTRACT_MULTIPLIER = 100  # standard equity/index option contract size


@dataclass
class Candidate:
    """net_debit_credit/max_profit/max_loss/payoff[].pnl are real per-contract
    dollars (i.e. already × CONTRACT_MULTIPLIER) -- what a trader actually
    pays/risks/makes trading one standard 100-share contract, matching every
    real broker/tool (confirmed against OptionStrat). Leg.mid stays per-share
    (that's how option premium is actually quoted, e.g. "$2.08"), and
    breakevens/payoff[].underlying stay as stock price levels, never scaled."""

    structure: str
    direction: str
    expiration: pd.Timestamp
    dte: int
    legs: list[Leg]
    net_debit_credit: float
    # None for structures where expiration-intrinsic-value math would be
    # outright wrong (currently just the calendar spread -- its long leg
    # still has real time value at the short leg's expiration, which plain
    # intrinsic value throws away). Every other structure always sets these.
    max_profit: float | None
    max_loss: float | None
    breakevens: list[float]
    approx_pop: float | None
    payoff: list[dict] = field(default_factory=list)
    # Calendar-only: the forward-variance IV-crush edge estimate (see
    # calendar_variance_edge()). None for every other structure.
    variance_edge: dict | None = None


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

    # Breakevens are a zero-crossing PRICE, found before scaling pnl to
    # dollars -- scaling wouldn't move where pnl==0 anyway, but reads clearer
    # working in the same per-share units the intrinsic-value math above uses.
    breakevens: list[float] = []
    for i in range(len(prices) - 1):
        p0, p1 = pnl[i], pnl[i + 1]
        if p0 == 0:
            breakevens.append(float(prices[i]))
        elif (p0 < 0 < p1) or (p1 < 0 < p0):
            frac = -p0 / (p1 - p0)
            breakevens.append(float(prices[i] + frac * (prices[i + 1] - prices[i])))

    pnl_dollars = pnl * CONTRACT_MULTIPLIER
    return {
        "net_debit_credit": net_debit_credit * CONTRACT_MULTIPLIER,
        "max_profit": float(max(0.0, pnl_dollars.max())),
        "max_loss": float(max(0.0, -pnl_dollars.min())),
        "breakevens": breakevens,
        "payoff": [{"underlying": float(p), "pnl": float(v)} for p, v in zip(prices, pnl_dollars)],
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


def build_cash_secured_put(chain: pd.DataFrame, expiration: pd.Timestamp, target_delta: float = 0.30) -> Candidate | None:
    """
    Sell one cash-secured put near `target_delta` -- undefined risk down to
    zero (secured by cash, not a bought wing), for a trader who's genuinely
    happy to own the stock at the strike, not just collecting premium with a
    hard stop. Closed-form P/L (no _summarize() curve-sampling needed -- a
    single leg's expiration payoff is linear past the strike, no benefit to
    sampling it): max_profit is the premium collected outright; max_loss is
    conventionally quoted as strike-premium (the capital at risk, i.e. the
    worst case if assigned and the stock went to zero); breakeven is
    strike-premium. Returns None if the chain can't support a put near
    target_delta, or the degenerate case where premium >= strike.
    """
    exp_chain = chain[chain["expiration"] == expiration].dropna(subset=["delta", "bid", "ask", "strikePrice"])
    if exp_chain.empty:
        return None
    primary = _nearest_to_delta(exp_chain, "PUT", target_delta)
    if primary is None:
        return None

    strike = float(primary["strikePrice"])
    premium = _mid(primary)
    max_loss = strike - premium
    if max_loss <= 0:
        return None

    leg = Leg("sell", "PUT", strike, float(primary["delta"]), premium)
    breakeven = strike - premium
    prices = np.linspace(strike * 0.92, strike * 1.08, 121)
    pnl = premium - np.maximum(strike - prices, 0.0)
    payoff = [{"underlying": float(p), "pnl": float(v) * CONTRACT_MULTIPLIER} for p, v in zip(prices, pnl)]
    return Candidate(
        structure="Cash Secured Put",
        direction="bullish",
        expiration=expiration,
        dte=int(exp_chain["dte"].iloc[0]),
        legs=[leg],
        net_debit_credit=-premium * CONTRACT_MULTIPLIER,
        max_profit=premium * CONTRACT_MULTIPLIER,
        max_loss=max_loss * CONTRACT_MULTIPLIER,
        breakevens=[breakeven],
        approx_pop=_approx_pop(exp_chain, [leg], [breakeven], is_credit=True),
        payoff=payoff,
    )


def build_covered_call(
    chain: pd.DataFrame, expiration: pd.Timestamp, underlying_price: float, target_delta: float = 0.30
) -> Candidate | None:
    """
    Sell one call near `target_delta` against 100 owned shares -- for a
    trader who's already long the stock (or willing to be) and thinks a big
    further rally is unlikely before this expiration. Same closed-form
    reasoning as build_cash_secured_put(): max_profit is the gap up to the
    strike plus premium (if called away); max_loss is stock_price-premium
    (conventional quoting, worst case the stock goes to zero); breakeven is
    stock_price-premium. `net_debit_credit` is the option premium only, not
    the stock's own cost basis -- matches how the rest of the app already
    keeps "what did the option leg cost" separate from position sizing.
    Returns None if the chain can't support a call near target_delta, or a
    degenerate case (e.g. the selected call is already at/below the current
    price -- not a sane "sell into a rally" setup).
    """
    if underlying_price is None or underlying_price <= 0:
        return None
    exp_chain = chain[chain["expiration"] == expiration].dropna(subset=["delta", "bid", "ask", "strikePrice"])
    if exp_chain.empty:
        return None
    primary = _nearest_to_delta(exp_chain, "CALL", target_delta)
    if primary is None:
        return None

    strike = float(primary["strikePrice"])
    premium = _mid(primary)
    max_profit = (strike - underlying_price) + premium
    max_loss = underlying_price - premium
    if max_profit <= 0 or max_loss <= 0:
        return None

    leg = Leg("sell", "CALL", strike, float(primary["delta"]), premium)
    breakeven = underlying_price - premium
    # NOT _approx_pop(): that shared helper's single-breakeven credit case
    # always returns 1-d, which is only right when the structure's loss
    # side matches what that leg's delta approximates (e.g. a bear call
    # spread: CALL delta ~ P(above breakeven) = P(loss), so P(profit)=1-d).
    # A covered call's profit direction is the *opposite* of a bear call
    # spread's despite using the same CALL leg -- it loses BELOW breakeven
    # (like a bull put spread), not above. CALL delta at the breakeven
    # already approximates P(price > breakeven) directly, which here IS
    # P(profit) -- using 1-d would silently invert this (confirmed by hand:
    # a breakeven sitting below the current spot produced a sub-50% POP
    # with 1-d, backwards from the obvious "already past breakeven" case).
    delta_at_breakeven = _delta_at_price(exp_chain, "CALL", breakeven)
    approx_pop = max(0.0, delta_at_breakeven) if delta_at_breakeven is not None else 0.0
    # Unlike build_cash_secured_put, this can't just sample the option leg
    # alone -- a naked short call's own payoff has unlimited loss above the
    # strike, which is only right once the +100 owned shares are added back
    # in (the whole point of "covered"). Padded around both spot and strike
    # (not spot alone) so the capped-upside plateau past the strike is
    # always visible even when the strike sits well above current price.
    lo = min(underlying_price, strike) * 0.85
    hi = max(underlying_price, strike) * 1.15
    prices = np.linspace(lo, hi, 121)
    stock_pnl = prices - underlying_price
    call_pnl = premium - np.maximum(prices - strike, 0.0)
    pnl = stock_pnl + call_pnl
    payoff = [{"underlying": float(p), "pnl": float(v) * CONTRACT_MULTIPLIER} for p, v in zip(prices, pnl)]
    return Candidate(
        structure="Covered Call",
        direction="bullish",
        expiration=expiration,
        dte=int(exp_chain["dte"].iloc[0]),
        legs=[leg],
        net_debit_credit=-premium * CONTRACT_MULTIPLIER,
        max_profit=max_profit * CONTRACT_MULTIPLIER,
        max_loss=max_loss * CONTRACT_MULTIPLIER,
        breakevens=[breakeven],
        approx_pop=approx_pop,
        payoff=payoff,
    )


def calendar_variance_edge(
    front_iv: float,
    front_dte: int,
    front_vega: float,
    back_iv: float,
    back_dte: int,
    back_vega: float,
) -> dict | None:
    """
    Forward-variance decomposition for a sell-front/buy-back-month calendar:
    isolates the "event" variance inflating the front-month leg's IV more
    than the back-month leg's (concentrated into fewer days), and estimates
    each leg's expected IV crush toward that shared post-event baseline
    (`iv_ex`). Real broker-supplied vega ($ per 1 IV point, e.g. Schwab's
    `vega` field -- already recorded per contract) turns each leg's expected
    crush into a dollar P&L estimate. This is a transparent, documented
    heuristic (assumes the same absolute event-variance lands in both
    expirations' IV) -- not a full option-pricing simulation, and not a
    guarantee, just an estimate of the trade's structural edge.

    IV_ex = sqrt[(IV_back^2*DTE_back - IV_front^2*DTE_front) / (DTE_back-DTE_front)]

    Returns None if there's no front/back ordering to decompose
    (front_dte >= back_dte) or if the term under the sqrt goes negative --
    the front leg isn't actually inflated relative to the back leg, so this
    formula has no measurable edge to report (not an error, just "no
    signal").
    """
    if front_dte >= back_dte:
        return None
    variance_term = (back_iv**2 * back_dte - front_iv**2 * front_dte) / (back_dte - front_dte)
    if variance_term < 0:
        return None
    iv_ex = variance_term**0.5

    front_crush = front_iv - iv_ex
    back_crush = back_iv - iv_ex
    # Short the front leg: an IV drop (positive front_crush) is a profit.
    # Long the back leg: an IV drop (positive back_crush) is a loss.
    front_vega_pnl = front_vega * front_crush * CONTRACT_MULTIPLIER
    back_vega_pnl = -back_vega * back_crush * CONTRACT_MULTIPLIER
    return {
        "iv_ex": iv_ex,
        "front_crush": front_crush,
        "back_crush": back_crush,
        "front_vega_pnl": front_vega_pnl,
        "back_vega_pnl": back_vega_pnl,
        "net_vega_pnl": front_vega_pnl + back_vega_pnl,
    }


def build_calendar_call(
    chain: pd.DataFrame,
    front_expiration: pd.Timestamp,
    back_expiration: pd.Timestamp,
    target_delta: float = 0.25,
) -> Candidate | None:
    """
    Sell a front-month call near `target_delta`, buy the same-delta call on a
    later `back_expiration` -- a classic pre-event calendar, structured to
    profit if the front leg's (typically event-inflated) IV crushes faster
    than the back leg's. Each leg is picked independently on its own
    expiration's chain slice (not pinned to the same strike), matching how a
    trader actually screens this: same delta, whatever strike that lands on
    each month.

    Deliberately does NOT compute max_profit/max_loss/breakevens/payoff via
    _summarize()'s intrinsic-value math -- that's only correct when every
    leg expires together, and the back leg here still has real time value at
    front_expiration. Returns those as None/empty instead of a wrong number.
    `variance_edge` (calendar_variance_edge()) is the real edge estimate for
    this structure. Returns None if either expiration's chain can't support
    a call near target_delta.
    """
    front_chain = chain[chain["expiration"] == front_expiration].dropna(
        subset=["delta", "bid", "ask", "strikePrice", "impliedVolatility", "vega"]
    )
    back_chain = chain[chain["expiration"] == back_expiration].dropna(
        subset=["delta", "bid", "ask", "strikePrice", "impliedVolatility", "vega"]
    )
    if front_chain.empty or back_chain.empty:
        return None

    front_row = _nearest_to_delta(front_chain, "CALL", target_delta)
    back_row = _nearest_to_delta(back_chain, "CALL", target_delta)
    if front_row is None or back_row is None:
        return None

    front_dte = int(front_row["dte"])
    back_dte = int(back_row["dte"])
    front_iv = float(front_row["impliedVolatility"])
    back_iv = float(back_row["impliedVolatility"])
    front_vega = float(front_row["vega"])
    back_vega = float(back_row["vega"])
    front_mid = _mid(front_row)
    back_mid = _mid(back_row)

    legs = [
        Leg(
            "sell", "CALL", float(front_row["strikePrice"]), float(front_row["delta"]), front_mid,
            expiration=front_expiration, implied_volatility=front_iv, vega=front_vega,
        ),
        Leg(
            "buy", "CALL", float(back_row["strikePrice"]), float(back_row["delta"]), back_mid,
            expiration=back_expiration, implied_volatility=back_iv, vega=back_vega,
        ),
    ]

    return Candidate(
        structure="Calendar Call Spread",
        direction="neutral",
        expiration=front_expiration,
        dte=front_dte,
        legs=legs,
        net_debit_credit=(back_mid - front_mid) * CONTRACT_MULTIPLIER,
        max_profit=None,
        max_loss=None,
        breakevens=[],
        approx_pop=None,
        payoff=[],
        variance_edge=calendar_variance_edge(front_iv, front_dte, front_vega, back_iv, back_dte, back_vega),
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
    How many contracts `capital` affords. candidate.max_loss is already a
    real per-contract dollar figure (see Candidate's docstring), not
    per-share, so no further ×100 here. None if capital doesn't cover even
    one contract (candidate.max_loss == 0 can't happen for a real spread
    with a nonzero width, but guarded anyway).
    """
    max_loss_per_contract = candidate.max_loss
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
        total_max_profit=contracts * candidate.max_profit,
        total_max_loss=contracts * candidate.max_loss,
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
