"""
src/dashboard/vol_surface.py

The implied-volatility surface: IV across strike AND expiration at once.

Why this exists
---------------
This app already reduces the surface to one number per expiration -- ATM IV
for the term structure, 25-delta skew, smile curvature. Those are the right
summaries for ranking symbols against each other, but a trader picking an
actual strike is looking at the thing the summaries came from. A skew of
+2.9 says puts are richer; it doesn't say whether that's a smooth smirk, a
kink at one strike, or a single stale quote dragging the fit. Only the
surface shows that.

Two deliberate modelling choices, both worth knowing before reading the
output:

**Moneyness, not strike.** Cells are keyed by (strike / spot - 1), so a
1-week and a 6-month expiration line up on the same axis and the surface
reads as one shape. Raw strikes can't be compared across expirations of
different underlyings, or even across time for one underlying once spot
moves.

**OTM side only.** Below spot the put's IV is used, above spot the call's.
That's the market convention and it isn't arbitrary: in-the-money options
are thinly traded and their quoted IVs are dominated by intrinsic value and
wide spreads, so mixing them in produces a surface with a spurious step at
the money. Put-call parity means the OTM side carries the same information
with better quotes.

Empty cells stay empty. No interpolation, no smoothing, no surface fit --
a gap in the grid means no contract was listed and quoted there, which is
itself worth seeing. Filling it would invent a volatility the market never
printed, which is exactly what this project refuses to do elsewhere.
"""

from __future__ import annotations

import pandas as pd

# Beyond a quarter from spot, listed strikes thin out and the quotes that
# remain are mostly spread rather than signal. Wide enough to show a real
# smirk's wings, narrow enough not to pad the grid with noise.
MONEYNESS_MIN = -0.25
MONEYNESS_MAX = 0.25
BUCKET_WIDTH = 0.025


def _bucket(moneyness: float) -> float:
    """Snap to the nearest bucket centre, so differing strike ladders across
    expirations land on one shared axis."""
    return round(round(moneyness / BUCKET_WIDTH) * BUCKET_WIDTH, 4)


def build_surface(chain: pd.DataFrame | None, spot: float | None, live_dte) -> dict:
    """
    Grid of implied vol by (days to expiry, moneyness).

    `live_dte` is passed in rather than computed here: the caller owns the
    "today" definition, and this project has already been bitten by a stale
    per-contract dte column baked in at fetch time.

    Returns expirations (each with dte and expiration date), the moneyness
    axis actually populated, and one cell per (dte, moneyness) that had a
    real quote. Cells carry which side they came from so the UI can say so.
    """
    empty = {"spot": spot, "expirations": [], "moneyness": [], "cells": [], "iv_min": None, "iv_max": None}
    if chain is None or chain.empty or not spot or spot <= 0:
        return empty

    required = {"impliedVolatility", "strikePrice", "optionType", "expiration"}
    if not required.issubset(chain.columns):
        return empty

    df = chain[list(required)].copy()
    # Schwab returns -999 as a no-quote sentinel; 500% is a generous sanity cap
    # (see data_quality.is_chain_usable, same reasoning).
    df = df[df["impliedVolatility"].notna() & (df["impliedVolatility"] > 0) & (df["impliedVolatility"] < 500)]
    df = df[df["strikePrice"].notna()]
    if df.empty:
        return empty

    df["moneyness"] = df["strikePrice"] / spot - 1.0
    df = df[(df["moneyness"] >= MONEYNESS_MIN) & (df["moneyness"] <= MONEYNESS_MAX)]
    if df.empty:
        return empty

    # OTM side only -- puts below spot, calls above. See the module docstring.
    wanted_side = df["moneyness"].apply(lambda m: "PUT" if m < 0 else "CALL")
    df = df[df["optionType"] == wanted_side]
    if df.empty:
        return empty

    df["expiration_str"] = df["expiration"].apply(lambda e: pd.Timestamp(e).date().isoformat())
    df["dte"] = df["expiration_str"].apply(live_dte)
    df = df[df["dte"] >= 0]
    if df.empty:
        return empty

    df["bucket"] = df["moneyness"].apply(_bucket)

    # More than one strike can land in a bucket on a dense ladder. Median
    # rather than mean: one stale wide quote shouldn't drag the cell.
    grouped = (
        df.groupby(["dte", "expiration_str", "bucket", "optionType"], as_index=False)
        .agg(iv=("impliedVolatility", "median"), contracts=("impliedVolatility", "size"))
    )

    cells = [
        {
            "dte": int(r.dte),
            "moneyness": float(r.bucket),
            "iv": float(r.iv),
            "side": r.optionType,
            "contracts": int(r.contracts),
        }
        for r in grouped.itertuples()
    ]
    if not cells:
        return empty

    expirations = (
        df[["dte", "expiration_str"]]
        .drop_duplicates()
        .sort_values("dte")
        .rename(columns={"expiration_str": "expiration"})
        .to_dict("records")
    )
    ivs = [c["iv"] for c in cells]

    return {
        "spot": float(spot),
        "expirations": [{"dte": int(e["dte"]), "expiration": e["expiration"]} for e in expirations],
        "moneyness": sorted({c["moneyness"] for c in cells}),
        "cells": cells,
        "iv_min": min(ivs),
        "iv_max": max(ivs),
    }
