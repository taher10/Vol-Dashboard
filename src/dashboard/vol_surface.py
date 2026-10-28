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

**Only quotes that can pin down a vol.** A contract worth four cents has an
implied volatility in the same sense that a coin flip has a trend: the
number exists, it is just not measuring anything. See
MAX_IV_BAND_VOL_POINTS for how that line is drawn and why it is drawn in
vol points rather than dollars. The count of what was removed is returned
alongside the grid, because a cell that is absent for this reason is
saying something different from one that was never quoted.

Empty cells stay empty. No interpolation, no smoothing, no surface fit --
a gap in the grid means nothing trustworthy was quoted there, which is
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

# How far apart the bid and the ask are allowed to be, measured in the vol
# points they imply, before the cell's IV stops meaning anything.
#
# A deep out-of-the-money contract near expiry is worth a few cents, and a
# few cents is almost all of its price. Observed on GOOGL at 4 DTE, 21% out
# of the money: bid $0.02, ask $0.06, vega 0.004, quoted IV 89.96. That
# spread spans (0.06 - 0.02) / 0.004 = 10 vol points, so the honest
# statement is "IV is somewhere around 85 to 95", and one tick of price
# moves it two and a half points. Printing 89.96 on a heatmap implies a
# precision the quote cannot support, and because these cells are always
# the most extreme ones they dominate the colour scale and make the real
# smirk unreadable.
#
# Dividing the spread by vega is what makes this comparable across the
# grid. A $0.04 spread is nothing on a $12 at-the-money option with vega
# 0.30 (0.13 vol points) and fatal on a $0.04 wing. A plain price floor
# does not work: on the 2026-10-26 snapshot, `mark < $0.10` catches 1.8% of
# contracts and a zero bid another 1.2%, while 17.6% have a spread wider
# than 5 vol points. The money is not the problem; the money *relative to
# vol sensitivity* is.
#
# 5.0 keeps roughly 82% of quoted contracts. On that snapshot the cells it
# removes have a median IV of 81.3 against 47.2 for the ones it keeps,
# which is the inflation this is here to remove -- and the surface's top
# end for GOOGL falls from 90.0 to 83.5 without touching the genuine wing
# quotes, which are wide in price but tight in vol terms.
MAX_IV_BAND_VOL_POINTS = 5.0

# Needed to judge quote quality. Absent on some live chains, in which case
# the surface is built unfiltered rather than not at all -- see _usable_quotes.
_QUOTE_COLUMNS = ("bid", "ask", "vega")


def _bucket(moneyness: float) -> float:
    """Snap to the nearest bucket centre, so differing strike ladders across
    expirations land on one shared axis."""
    return round(round(moneyness / BUCKET_WIDTH) * BUCKET_WIDTH, 4)


def _usable_quotes(df: pd.DataFrame, max_iv_band: float) -> tuple[pd.DataFrame, int, bool]:
    """Drop contracts whose quote cannot pin down a vol. (kept, dropped, applied).

    Two tests, both about whether a printed IV is knowable rather than
    whether the trade is attractive:

      * A zero bid means nobody is offering to buy it. There is no price,
        only an ask, and an IV derived from one side of a market is a guess.
      * A spread wider than `max_iv_band` vol points means the quote is
        consistent with a wide range of vols, so the single number the
        broker reports is a point estimate of something that is not a point.

    Rows missing bid, ask or vega are dropped too: the broker withholding a
    greek on a contract it is still quoting is itself a sign of a quote not
    worth reading, and keeping them would reintroduce exactly the cells this
    removes.

    Skipped entirely when the columns are absent, or when applying it would
    empty the grid. A live chain that arrives without greeks should produce
    the old unfiltered surface, not a blank one -- a filter that silently
    deletes a whole page is worse than the noise it was added to remove.
    """
    if max_iv_band <= 0 or not set(_QUOTE_COLUMNS).issubset(df.columns):
        return df, 0, False

    bid = pd.to_numeric(df["bid"], errors="coerce")
    ask = pd.to_numeric(df["ask"], errors="coerce")
    vega = pd.to_numeric(df["vega"], errors="coerce")

    # vega.where(vega > 0) makes a zero or negative vega produce NaN rather
    # than an infinity, so it fails the `<=` test instead of passing it.
    band = (ask - bid) / vega.where(vega > 0)

    # The escape hatch asks whether the filter can judge anything, not
    # whether anything survived it. Those differ, and conflating them was a
    # bug: a window where every contract is genuinely unquotable should come
    # back empty -- that is the honest answer and the count says why --
    # whereas a chain that arrived with no greeks at all should be built
    # unfiltered, because the fault is in the data rather than the market.
    if not band.notna().any():
        return df, 0, False

    keep = (bid > 0) & band.notna() & (band <= max_iv_band)
    return df[keep], int((~keep).sum()), True


def build_surface(
    chain: pd.DataFrame | None,
    spot: float | None,
    live_dte,
    max_iv_band: float = MAX_IV_BAND_VOL_POINTS,
) -> dict:
    """
    Grid of implied vol by (days to expiry, moneyness).

    `live_dte` is passed in rather than computed here: the caller owns the
    "today" definition, and this project has already been bitten by a stale
    per-contract dte column baked in at fetch time.

    Returns expirations (each with dte and expiration date), the moneyness
    axis actually populated, and one cell per (dte, moneyness) that had a
    real quote. Cells carry which side they came from so the UI can say so.
    """
    empty = {
        "spot": spot,
        "expirations": [],
        "moneyness": [],
        "cells": [],
        "iv_min": None,
        "iv_max": None,
        "quote_filter": {"applied": False, "max_iv_band": max_iv_band, "excluded_contracts": 0},
    }
    if chain is None or chain.empty or not spot or spot <= 0:
        return empty

    required = {"impliedVolatility", "strikePrice", "optionType", "expiration"}
    if not required.issubset(chain.columns):
        return empty

    # Quote columns come along when present so _usable_quotes can read them;
    # the surface itself does not use them.
    df = chain[list(required) + [c for c in _QUOTE_COLUMNS if c in chain.columns]].copy()
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

    # After the OTM cut, so the count reports contracts that would otherwise
    # have been drawn rather than every unquotable row in the chain.
    df, excluded_contracts, filter_applied = _usable_quotes(df, max_iv_band)
    if df.empty:
        # Everything in the window failed. That is a real answer -- "nothing
        # quoted here is worth reading" -- so the count has to survive into
        # the response, or the UI draws a blank grid with no account of why
        # it is blank, which is the ambiguity this whole change removes.
        return {
            **empty,
            "quote_filter": {
                "applied": filter_applied,
                "max_iv_band": max_iv_band,
                "excluded_contracts": excluded_contracts,
            },
        }

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
        "quote_filter": {
            "applied": filter_applied,
            "max_iv_band": max_iv_band,
            "excluded_contracts": excluded_contracts,
        },
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
    max_iv_band: float = MAX_IV_BAND_VOL_POINTS,
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
    now = build_surface(current_chain, current_spot, live_dte, max_iv_band=max_iv_band)
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
    prior = build_surface(
        prior_chain, prior_spot, lambda e: int(dte_by_exp.get(e, -1)), max_iv_band=max_iv_band
    )
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
