"""
src/data_quality.py

Shared validity check for a freshly-fetched options chain, used on both the
write side (src/job.py — don't persist a bad live pull over good data) and
the read side (src/dashboard/data_loader.py — don't serve a bad saved
snapshot when an older good one exists).

Why this exists: Schwab's API returns -999 as a sentinel for
impliedVolatility/delta/gamma/theta/vega/rho when it has no live quote to
compute Greeks from (confirmed: happens on live pulls outside regular
market hours) -- a chain full of -999s parses fine and isn't empty, so
nothing upstream naturally rejects it, but every metric computed from it is
garbage (all-NaN term structure, no skew/curvature).
"""

from __future__ import annotations

from datetime import datetime, time as _time, UTC
from zoneinfo import ZoneInfo

import pandas as pd

_MARKET_TZ = ZoneInfo("America/New_York")
_MARKET_OPEN = _time(9, 30)
_MARKET_CLOSE = _time(16, 0)


class LiveDataUnavailableError(Exception):
    """Raised when a live Schwab pull returned a chain with no usable quotes
    (e.g. outside market hours) -- callers should fall back to the last
    saved good snapshot rather than treat this as a hard failure."""


def is_chain_usable(chain: pd.DataFrame | None, min_valid_fraction: float = 0.5) -> bool:
    """
    True if at least `min_valid_fraction` of `chain`'s rows have a real
    (non-sentinel, positive, sane) impliedVolatility -- i.e. Schwab actually
    had live quotes to compute Greeks from. 0.5 rather than requiring 100%
    usable: a handful of illiquid far-dated contracts can legitimately lack
    a quote even on an otherwise-good pull, and that shouldn't trigger a
    fallback.
    """
    if chain is None or chain.empty or "impliedVolatility" not in chain.columns:
        return False
    iv = chain["impliedVolatility"]
    valid = iv.notna() & (iv > 0) & (iv < 500)  # -999 sentinel is excluded by > 0 alone; 500% is a generous sanity cap
    return bool(valid.mean() >= min_valid_fraction)


def is_regular_market_hours(now: datetime | None = None) -> bool:
    """
    Rough regular-hours check (9:30am-4:00pm US/Eastern, Mon-Fri). Doesn't
    know about market holidays -- there's no maintained holiday calendar to
    check against here -- so it reads a real holiday that falls on a weekday
    as "should be open." That's fine for its one purpose: telling apart "a
    quote gap during a normal trading session" (worth flagging as a likely
    real problem) from "a quote gap outside trading hours" (the expected,
    benign case) when a chain comes back unusable -- see is_chain_usable's
    caller in job.py. A holiday still correctly produces a genuine gap this
    can't explain the cause of, which is a smaller, safer failure mode than
    silently reassuring "likely outside market hours" during a real outage.
    """
    now = (now or datetime.now(UTC)).astimezone(_MARKET_TZ)
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return _MARKET_OPEN <= now.time() <= _MARKET_CLOSE
