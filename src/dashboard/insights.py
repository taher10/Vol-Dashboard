"""
src/dashboard/insights.py

Turns score_expiries() output into plain-language commentary: what a
richness/skew combination usually means in practice, and whether the
premium it implies is actually worth selling or buying -- backed by a real
example trade computed via strategy_engine.build_vertical(), not just
qualitative text. Pure pandas/no I/O, same house style as decision_engine.py
and strategy_engine.py.

Two entry points:
  expiry_commentary()        Overview / Expiry Drilldown -- full read on one
                              expiry: what the setup means + a worked example.
  recommendation_commentary() Strategy Builder -- shorter, tied to the actual
                              recommended trade rather than a generic example.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.dashboard import strategy_engine
from src.dashboard.strategy_engine import Candidate

# (richness_label, skew_bias) -> interpretation template. `{basis}` is filled
# in per-call with whichever signal score_expiries() actually used for this
# row -- "its own historical IV range" (trailing IV z-score, the primary
# signal now that price history isn't collected) or "its own realized vol"
# (VRP, still used when available) -- so the copy never claims a basis that
# wasn't actually computed. Numbers (dte, richness_z) are woven into the
# headline separately, not into these templates, so the template bank stays
# small (9 cells) instead of combinatorial.
_INTERPRETATIONS: dict[tuple[str, str], str] = {
    ("Rich", "Puts richer"): (
        "IV here is elevated versus {basis}, and puts are pricing "
        "richer than calls the same distance from the money. That combination usually "
        "means the market is pricing in real downside concern -- an event on the "
        "calendar, macro or crash-hedging demand, or genuine perceived tail risk -- not "
        "just an overpriced option."
    ),
    ("Rich", "Calls richer"): (
        "IV here is elevated versus {basis}, with calls pricing "
        "richer than puts. This usually points to upside catalyst risk -- an event, "
        "earnings, a possible squeeze -- rather than crash risk."
    ),
    ("Rich", "Balanced"): (
        "IV here is elevated versus {basis}, with no directional "
        "skew bias -- both sides are pricing extra premium roughly equally. That reads "
        "as volatility-event risk with uncertain direction, not a directional call."
    ),
    ("Cheap", "Puts richer"): (
        "IV here is low versus {basis}, but puts still carry a "
        "premium over calls -- some downside skew is priced even while overall vol "
        "looks cheap."
    ),
    ("Cheap", "Calls richer"): (
        "IV here is low versus {basis}, with calls pricing richer "
        "than puts -- a mild upside-catalyst premium sitting on top of otherwise cheap "
        "volatility."
    ),
    ("Cheap", "Balanced"): (
        "IV here is low versus {basis} with no skew bias either "
        "way -- the market isn't pricing much drama in either direction."
    ),
    ("Neutral", "Puts richer"): (
        "IV here is roughly in line with {basis}, with a modest "
        "downside skew tilt -- puts carry a small premium over calls, but nothing "
        "extreme."
    ),
    ("Neutral", "Calls richer"): (
        "IV here is roughly in line with {basis}, with a modest "
        "upside skew tilt -- calls carry a small premium over puts, but nothing "
        "extreme."
    ),
    ("Neutral", "Balanced"): (
        "Nothing stands out here -- IV is in line with {basis} and "
        "the smile is roughly symmetric."
    ),
}

# Which vertical to build as the "worth it?" example for each cell -- None
# where there's no clear directional premium edge from the smile alone.
_EXAMPLE_STRUCTURE: dict[tuple[str, str], tuple[str, str] | None] = {
    ("Rich", "Puts richer"): ("bullish", "credit"),  # Bull Put Spread -- sells the rich put premium
    ("Rich", "Calls richer"): ("bearish", "credit"),  # Bear Call Spread -- sells the rich call premium
    ("Rich", "Balanced"): None,
    ("Cheap", "Puts richer"): ("bearish", "debit"),  # Bear Put Spread -- buys cheap-but-skewed puts
    ("Cheap", "Calls richer"): ("bullish", "debit"),  # Bull Call Spread -- buys cheap-but-skewed calls
    ("Cheap", "Balanced"): None,
    ("Neutral", "Puts richer"): ("bullish", "credit"),
    ("Neutral", "Calls richer"): ("bearish", "credit"),
    ("Neutral", "Balanced"): None,
}


@dataclass
class Commentary:
    headline: str
    interpretation: str
    trade_angle: str
    example_trade: Candidate | None


def _pop_judgment(max_profit: float, max_loss: float) -> str:
    """
    A short qualitative read of the reward:risk ratio -- deliberately coarse
    (3 buckets) since a single ratio number doesn't need a false-precision
    label. Calibrated against real numbers seen this session: a moderate
    30-delta credit spread lands ~0.35-0.4 ("reasonable"), a conservative
    20-delta far-OTM one lands ~0.1 ("thin") -- matches the real-world
    intuition that high-POP/far-OTM premium-selling has poor reward:risk by
    construction, not a hidden edge.
    """
    if max_loss <= 0:
        return "the math here doesn't resolve to a real ratio -- treat this example skeptically"
    ratio = max_profit / max_loss
    if ratio >= 0.35:
        return "a reasonable edge if you're comfortable with the view the market's pricing in"
    if ratio >= 0.15:
        return "a modest edge -- worth it only with real conviction in the view, not just because the premium looks rich"
    return (
        "thin compensation for the risk being priced -- the size of the premium here "
        "mostly just reflects the size of the risk, not an exploitable mispricing"
    )


def expiry_commentary(
    chain: pd.DataFrame,
    score_row: pd.Series,
    target_short_delta: float = 0.30,
    width_strikes: int = 2,
) -> Commentary:
    """
    Turn one score_expiries() row into plain-language commentary plus (where
    the smile shows a clear directional premium) a real example trade -- via
    the exact same strategy_engine.build_vertical() Strategy Builder itself
    calls, so the numbers here and there are always consistent by
    construction, never a second hand-derived estimate.
    """
    richness = str(score_row["richness_label"])
    skew_bias = str(score_row["skew_bias"])
    dte = int(score_row["dte"])
    expiration = pd.Timestamp(score_row["expiration"])
    richness_z = score_row.get("richness_z")
    richness_z = float(richness_z) if richness_z is not None and pd.notna(richness_z) else None
    basis = "its own realized vol" if score_row.get("richness_basis") == "vrp" else "its own historical IV range"

    key = (richness, skew_bias)
    template = _INTERPRETATIONS.get(key)
    interpretation = template.format(basis=basis) if template else "Not enough signal here to characterize the setup."

    headline = f"{expiration.strftime('%b %d')} ({dte}d) is {richness.lower()}"
    if richness_z is not None:
        headline += f" (z={richness_z:+.1f})"
    headline += f" vs. {basis}, {skew_bias.lower()} skew."

    structure_spec = _EXAMPLE_STRUCTURE.get(key)
    example_trade: Candidate | None = None
    if structure_spec is not None:
        direction, structure = structure_spec
        example_trade = strategy_engine.build_vertical(
            chain, expiration, direction, structure, target_short_delta, width_strikes
        )

    if example_trade is not None:
        judgment = _pop_judgment(example_trade.max_profit, example_trade.max_loss)
        legs_desc = ", ".join(f"{leg.action} {leg.optionType} {leg.strike:g}" for leg in example_trade.legs)
        verb = "collect" if example_trade.net_debit_credit < 0 else "cost"
        trade_angle = (
            f"A {example_trade.structure} here ({legs_desc}) would {verb} "
            f"${abs(example_trade.net_debit_credit):.2f}, risking ${example_trade.max_loss:.2f} to make "
            f"${example_trade.max_profit:.2f} (~{example_trade.approx_pop * 100:.0f}% approx POP) — {judgment}."
        )
    else:
        trade_angle = (
            "No clear directional premium edge from the smile alone at this expiry -- "
            "nothing here strongly favors selling or buying premium outright."
        )

    return Commentary(
        headline=headline,
        interpretation=interpretation,
        trade_angle=trade_angle,
        example_trade=example_trade,
    )


def recommendation_commentary(score_row: pd.Series | None, candidate: Candidate) -> str:
    """
    Shorter, single-paragraph variant for Strategy Builder: given the
    recommended trade Strategy Builder already picked (risk-appetite-driven,
    so its structure/delta may differ from expiry_commentary's generic
    example), explain whether it lines up with or cuts against the current
    vol backdrop at that expiry.
    """
    if score_row is None:
        return f"{candidate.structure} at {candidate.dte}d, sized to your stated risk appetite and capital."

    richness = str(score_row.get("richness_label", "Neutral"))
    skew_bias = str(score_row.get("skew_bias", "Balanced"))
    is_credit = candidate.net_debit_credit < 0
    is_put_side = candidate.legs[0].optionType == "PUT"

    skew_match = (is_put_side and skew_bias == "Puts richer") or (not is_put_side and skew_bias == "Calls richer")
    aligned = (skew_match and richness in ("Rich", "Neutral")) if is_credit else (skew_match and richness == "Cheap")

    basis = "its own realized vol" if score_row.get("richness_basis") == "vrp" else "its own historical IV range"
    backdrop = f"this expiry is {richness.lower()} vs. {basis} with a {skew_bias.lower()} skew tilt"
    if aligned:
        return (
            f"This lines up with the vol backdrop: {backdrop}, which is exactly the kind of setup "
            f"this structure is built to {'collect' if is_credit else 'buy cheaply'}."
        )
    return (
        f"Worth noting: {backdrop} -- this structure isn't the textbook fit for that backdrop, "
        f"so the case for it here rests more on your stated view than on the vol surface itself."
    )
