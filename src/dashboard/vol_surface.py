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


# Standard maturity ladder for comparing two dates. See build_comparison.
DTE_BUCKETS = [7, 14, 30, 45, 60, 90, 120, 180, 270, 365]

_STORED_COLUMN_MAP = {
    "implied_volatility": "impliedVolatility",
    "strike_price": "strikePrice",
    "option_type": "optionType",
    "underlying_price": "underlyingPrice",
}


def normalize_stored_chain(df: pd.DataFrame) -> pd.DataFrame:
    """Rename SchwabDatabase.options_snapshot()'s snake_case columns to the
    camelCase the live chain uses, so one surface implementation serves both."""
    return df.rename(columns=_STORED_COLUMN_MAP)


def _nearest_bucket(dte: int) -> int:
    return min(DTE_BUCKETS, key=lambda b: abs(b - dte))


def build_comparison(
    current_chain: pd.DataFrame | None,
    prior_chain: pd.DataFrame | None,
    current_spot: float | None,
    prior_spot: float | None,
    live_dte,
) -> dict:
    """
    How the surface repriced between two snapshots.

    Two choices that decide whether the numbers mean anything:

    **Constant maturity, not same expiration.** A contract that was 32 DTE on
    the earlier snapshot is fewer DTE now, so diffing the same expiration
    blends the vol move with plain roll-down and reads as a change that
    didn't happen. Both sides are snapped to a standard maturity ladder
    instead, which answers "what is 30-day vol doing" rather than "what
    happened to that one contract".

    **Each side's dte measured as of its own snapshot.** The current chain
    uses live_dte (today's real date, see _live_dte's own warning about the
    frozen column), but the prior chain deliberately uses its stored `dte`
    column: that value was correct on the day it was fetched, which is
    exactly the maturity that snapshot was quoting. Recomputing it against
    today would shift every historical expiry and silently misalign the grid.

    Moneyness handles the other half: it's relative to each snapshot's own
    spot, so a 10%-OTM put is compared against what was then a 10%-OTM put
    even though spot moved in between.

    Cells appear only where both sides quoted something. A strike listed
    today but not a fortnight ago has no change to report, and inventing a
    baseline for it would manufacture a move.
    """
    now = build_surface(current_chain, current_spot, live_dte)
    if not now["cells"] or prior_chain is None or prior_chain.empty or not prior_spot:
        return {"cells": [], "change_min": None, "change_max": None}

    # build_surface needs a per-expiration dte. For a stored chain the row's own
    # dte column is the honest one -- it was correct on the day it was fetched.
    dte_by_exp = (
        prior_chain.assign(_e=prior_chain["expiration"].apply(lambda e: pd.Timestamp(e).date().isoformat()))
        .groupby("_e")["dte"]
        .median()
        .to_dict()
    )
    prior = build_surface(prior_chain, prior_spot, lambda e: int(dte_by_exp.get(e, -1)))
    if not prior["cells"]:
        return {"cells": [], "change_min": None, "change_max": None}

    def bucketed(cells):
        out: dict[tuple[int, float], list[float]] = {}
        for c in cells:
            out.setdefault((_nearest_bucket(c["dte"]), c["moneyness"]), []).append(c["iv"])
        return {k: sum(v) / len(v) for k, v in out.items()}

    a, b = bucketed(now["cells"]), bucketed(prior["cells"])
    cells = [
        {
            "dte": k[0],
            "moneyness": k[1],
            "iv": round(a[k], 4),
            "iv_prev": round(b[k], 4),
            "change": round(a[k] - b[k], 4),
        }
        for k in sorted(a.keys() & b.keys())
    ]
    if not cells:
        return {"cells": [], "change_min": None, "change_max": None}

    changes = [c["change"] for c in cells]
    return {
        "cells": cells,
        "change_min": min(changes),
        "change_max": max(changes),
        "buckets": sorted({c["dte"] for c in cells}),
        "moneyness": sorted({c["moneyness"] for c in cells}),
    }
