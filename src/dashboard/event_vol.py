"""
src/dashboard/event_vol.py

How large a one-off move the options market is pricing for a known event.

Why this exists
---------------
Trade Ideas ranks by richness -- how far a symbol's premium sits from its
own history -- and that ranking is dominated by names with earnings in the
next week or two. It has to be: implied vol rises before earnings because
a binary event is genuinely coming, so a pre-earnings name looks "rich" by
construction. Eight cash-secured puts on eight different tickers can all be
the same trade, and selling that premium is not an edge, it is being paid
to carry event risk.

Flagging those ideas requires knowing how much of the premium is the event.
The term structure already says: implied vol for an expiry that contains
the event is elevated against one that does not, and the difference in
total variance between them is the event's contribution.

    variance(T)            = (IV/100)^2 * T/365
    event variance         = variance(after) - variance(before)   [excess over baseline]
    implied one-off move   = sqrt(event variance)

Quoted as a percentage of spot, which is how a trader states it: "the
market is pricing a 7% move on earnings."

Why the event date is required
------------------------------
An earlier version tried to *locate* the event by scanning for the largest
forward-variance spike. It found 8.6% for META and 7.1% for TSLA correctly,
and then reported a 4.9% "event" for SPX at 44-59 DTE -- an index has no
earnings, and that was a quirk of the long end being thin. Given the date
from the calendar, the window is known and there is nothing to guess, so
the number is either grounded or absent.

This is a decomposition of quoted prices, not a forecast. It says what the
market is charging, never whether that is too much -- see
earnings.py for why comparing it to history is a separate and much weaker
claim.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import pandas as pd


@dataclass(frozen=True)
class EventMove:
    """The market's implied one-off move for an event, and its workings."""

    implied_move_pct: float
    before_dte: int | None  # last expiry that does NOT contain the event
    after_dte: int          # first expiry that DOES
    before_iv: float | None
    after_iv: float
    baseline_iv: float      # the long-dated vol the excess is measured against


def _atm_iv(chain: pd.DataFrame, expiration) -> float | None:
    """ATM implied vol for one expiry, taken from the call nearest 0.50 delta."""
    side = chain[(chain["expiration"] == expiration) & (chain["optionType"] == "CALL")]
    side = side.dropna(subset=["delta", "impliedVolatility"])
    side = side[(side["impliedVolatility"] > 0) & (side["impliedVolatility"] < 500)]
    if side.empty:
        return None
    nearest = side.iloc[(side["delta"] - 0.50).abs().argsort()].iloc[0]
    return float(nearest["impliedVolatility"])


def implied_event_move(
    chain: pd.DataFrame | None,
    live_dte,
    event_date: date,
    today: date,
) -> EventMove | None:
    """
    What the chain implies for a one-off move on `event_date`.

    Returns None rather than a number whenever the chain cannot support the
    calculation: no expiry spanning the event, too few expiries to
    establish a baseline, no usable ATM quotes, or an event already past.
    A missing flag is honest; a fabricated one is not.

    `live_dte` is injected rather than computed here for the same reason it
    is everywhere else in this project -- the chain's stored `dte` column is
    frozen at fetch time and goes stale (see routes._live_dte).
    """
    if chain is None or chain.empty or "expiration" not in chain.columns:
        return None

    event_dte = (event_date - today).days
    if event_dte < 0:
        return None  # already happened; nothing left to price

    quotes: list[tuple[int, float]] = []
    for expiration in chain["expiration"].dropna().unique():
        dte = live_dte(expiration)
        if dte <= 0:
            continue
        iv = _atm_iv(chain, expiration)
        if iv is not None:
            quotes.append((dte, iv))
    quotes.sort()
    if len(quotes) < 2:
        return None

    after = next((q for q in quotes if q[0] >= event_dte), None)
    if after is None:
        return None  # every listed expiry settles before the event
    before = next((q for q in reversed(quotes) if q[0] < event_dte), None)

    # The longest-dated vol is the least contaminated by any single event,
    # so it stands in for "what this name's vol looks like without one".
    baseline_dte, baseline_iv = quotes[-1]
    if after[0] == baseline_dte and len(quotes) > 1:
        baseline_dte, baseline_iv = quotes[-2]

    after_dte, after_iv = after
    total = (after_iv / 100) ** 2 * (after_dte / 365)

    if before is None:
        # The event falls inside the nearest expiry, so there is no
        # event-free window to subtract. The baseline covers the rest.
        non_event = (baseline_iv / 100) ** 2 * (after_dte / 365)
        before_dte = before_iv = None
    else:
        before_dte, before_iv = before
        non_event = (before_iv / 100) ** 2 * (before_dte / 365) + (baseline_iv / 100) ** 2 * (
            (after_dte - before_dte) / 365
        )

    excess = total - non_event
    if excess <= 0:
        return None  # the event-spanning expiry is not actually bid up

    return EventMove(
        implied_move_pct=math.sqrt(excess) * 100,
        before_dte=before_dte,
        after_dte=after_dte,
        before_iv=before_iv,
        after_iv=after_iv,
        baseline_iv=baseline_iv,
    )
