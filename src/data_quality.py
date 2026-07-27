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

import pandas as pd


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
