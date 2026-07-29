"""
src/symbols.py

SYMBOL_REGISTRY -- the set of symbols the dashboard/pipeline knows about and
the per-symbol fetch parameters each one needs. Deliberately has no Streamlit
import (unlike src/dashboard/app.py, where this used to live) so headless
scripts -- the daily GitHub Actions snapshot job, in particular -- can import
it without pulling in the whole Streamlit runtime.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SymbolInfo:
    """Registry entry for one symbol.

    api_symbol        : exact string Schwab's API expects. Indices need a
                         "$" prefix (e.g. "$SPX"); equities don't ("AAPL").
                         Auto-prepending "$" to every symbol (the old
                         behavior) silently broke every non-index ticker.
    strike_increment  : passed through to fetch_monthly_chain's post-fetch
                         strike filter. SPX is validated at $100 spacing;
                         None skips that filter for equities, since a single
                         fixed increment doesn't fit stocks at very different
                         price levels the way $100 fits SPX.
    strikes_each_side : baseline strike count on each side of ATM. SPX is
                         validated at 5 (comfortably brackets 25-delta at
                         $100 spacing). Equities need far more -- empirically
                         20 reliably brackets 25-delta even for GOOGL/TSLA
                         (the tightest cases found; verified live), since a
                         fixed strike COUNT covers a much smaller $ distance
                         from ATM at an equity's tighter native spacing.
                         Without this, skew/curvature come back NaN for most
                         expiries -- confirmed: GOOGL/TSLA were 0/13 valid
                         rows at SPX's n_side=5 default before this fix.
    color             : fixed per symbol identity (not per selection order),
                         so a symbol's line color is stable across sessions
                         and re-renders -- slot 1 (SPX, blue) matches the
                         app's pre-existing single-symbol default color.
                         Order/hues from the dataviz skill's validated
                         8-color categorical palette (references/palette.md),
                         used unmodified. That palette is only validated for
                         8 slots -- a 9th color is never a genuinely new,
                         CVD-safe hue, so symbols past the 8th cycle back
                         through the same 8 rather than inventing one.
    """

    api_symbol: str
    strike_increment: int | None
    strikes_each_side: int
    color: str


SYMBOL_REGISTRY: dict[str, SymbolInfo] = {
    "SPX":   SymbolInfo("$SPX", 100, 5, "#2a78d6"),    # slot 1 blue
    "AAPL":  SymbolInfo("AAPL", None, 20, "#eb6834"),  # slot 2 orange
    "MSFT":  SymbolInfo("MSFT", None, 20, "#1baf7a"),  # slot 3 aqua
    "GOOGL": SymbolInfo("GOOGL", None, 20, "#eda100"), # slot 4 yellow
    "AMZN":  SymbolInfo("AMZN", None, 20, "#e87ba4"),  # slot 5 magenta
    "NVDA":  SymbolInfo("NVDA", None, 20, "#008300"),  # slot 6 green
    "META":  SymbolInfo("META", None, 20, "#4a3aa7"),  # slot 7 violet
    "TSLA":  SymbolInfo("TSLA", None, 20, "#e34948"),  # slot 8 red
    # Past 8, cycle the same validated hues rather than inventing new ones
    # (see SymbolInfo.color docstring above).
    "MU":    SymbolInfo("MU", None, 20, "#2a78d6"),    # slot 1 blue (cycled)
    "AMD":   SymbolInfo("AMD", None, 20, "#eb6834"),   # slot 2 orange (cycled)
    "MRVL":  SymbolInfo("MRVL", None, 20, "#1baf7a"),  # slot 3 aqua (cycled)
    "SNDK":  SymbolInfo("SNDK", None, 20, "#eda100"),  # slot 4 yellow (cycled)
    "COHR":  SymbolInfo("COHR", None, 20, "#e87ba4"),  # slot 5 magenta (cycled)
    "BE":    SymbolInfo("BE", None, 20, "#008300"),    # slot 6 green (cycled)
    "SPCX":  SymbolInfo("SPCX", None, 20, "#4a3aa7"),  # slot 7 violet (cycled)
    "NBIS":  SymbolInfo("NBIS", None, 20, "#e34948"),  # slot 8 red (cycled)
    "PLTR":  SymbolInfo("PLTR", None, 20, "#2a78d6"),  # slot 1 blue (cycled)
    "JPM":   SymbolInfo("JPM", None, 20, "#eb6834"),   # slot 2 orange (cycled)
    "LITE":  SymbolInfo("LITE", None, 20, "#1baf7a"),  # slot 3 aqua (cycled)
    "NFLX":  SymbolInfo("NFLX", None, 20, "#eda100"),  # slot 4 yellow (cycled)
}
