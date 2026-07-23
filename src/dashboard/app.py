"""
src/dashboard/app.py

Streamlit multipage entrypoint (Overview page). Also hosts the shared
setup logic (`AppConfig`, `render_sidebar`, cached snapshot loading, error
handling) that `src/dashboard/pages/*.py` import, since those page scripts
run independently and need the same sidebar/session-state bootstrapping.

Everything below `main()` that touches Streamlit's UI is wrapped in
functions, and the actual page rendering only fires when this file is run
as the active script (`if __name__ == "__main__":`) — importing this
module from a page file (to reuse `render_sidebar`/`get_snapshot`/etc.)
does NOT re-render the Overview page's content.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# Ensure project root is on sys.path regardless of how/where Streamlit
# executes this script from (mirrors data_loader.py's own bootstrap).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd
import streamlit as st

from src.auth import write_token_from_base64
from src.dashboard import chart_components, data_loader, decision_engine
from src.dashboard.data_loader import SnapshotBundle
from src.dashboard.decision_engine import ContractFilters, ScoreWeights

DEFAULT_SAVE_SYMBOL = "SPX"


@dataclass(frozen=True)
class SymbolInfo:
    """Registry entry for a symbol the sidebar can select.

    api_symbol       : exact string Schwab's API expects. Indices need a
                        "$" prefix (e.g. "$SPX"); equities don't ("AAPL").
                        Auto-prepending "$" to every symbol (the old
                        behavior) silently broke every non-index ticker.
    strike_increment : passed through to fetch_monthly_chain's post-fetch
                        strike filter. SPX is validated at $100 spacing;
                        None skips that filter for equities, since a single
                        fixed increment doesn't fit stocks at very different
                        price levels the way $100 fits SPX.
    color            : fixed per symbol identity (not per selection order),
                        so a symbol's line color is stable across sessions
                        and re-renders -- slot 1 (SPX, blue) matches the
                        app's pre-existing single-symbol default color.
                        Order/hues from the dataviz skill's validated
                        8-color categorical palette (references/palette.md),
                        used unmodified.
    """

    api_symbol: str
    strike_increment: int | None
    color: str


SYMBOL_REGISTRY: dict[str, SymbolInfo] = {
    "SPX":   SymbolInfo("$SPX", 100, "#2a78d6"),   # slot 1 blue
    "AAPL":  SymbolInfo("AAPL", None, "#eb6834"),  # slot 2 orange
    "MSFT":  SymbolInfo("MSFT", None, "#1baf7a"),  # slot 3 aqua
    "GOOGL": SymbolInfo("GOOGL", None, "#eda100"), # slot 4 yellow
    "AMZN":  SymbolInfo("AMZN", None, "#e87ba4"),  # slot 5 magenta
    "NVDA":  SymbolInfo("NVDA", None, "#008300"),  # slot 6 green
    "META":  SymbolInfo("META", None, "#4a3aa7"),  # slot 7 violet
    "TSLA":  SymbolInfo("TSLA", None, "#e34948"),  # slot 8 red
}


_SECRET_ENV_KEYS = (
    "SCHWAB_API_KEY", "SCHWAB_APP_SECRET", "SCHWAB_CALLBACK_URL",
    "TOKEN_PATH", "SCHWAB_TIMEOUT", "SCHWAB_TOKEN_B64",
)


def _bootstrap_secrets() -> None:
    """Copy Schwab config from st.secrets into os.environ, then re-materialize
    token.json from SCHWAB_TOKEN_B64 if no token is on disk yet.

    SchwabAuth.from_env() (src/auth.py) reads os.environ directly and has no
    knowledge of Streamlit -- Streamlit does not guarantee st.secrets values
    are already mirrored into the process environment by the time this
    module-level code runs, so copy them explicitly rather than relying on
    that. Also handles token.json: Streamlit Community Cloud's disk is
    ephemeral, so a token produced by a local `--first-time` OAuth run won't
    survive a redeploy/sleep-wake cycle. Runs at import time (before any page
    renders) so the "Refresh Live Data" button has credentials/a token to
    work with. No-ops locally where no secrets.toml exists.
    """
    try:
        secrets = st.secrets
    except Exception:
        return
    for key in _SECRET_ENV_KEYS:
        if key in os.environ:
            continue
        try:
            value = secrets.get(key)
        except Exception:
            value = None
        if value:
            os.environ[key] = str(value)

    b64 = os.environ.get("SCHWAB_TOKEN_B64", "")
    if b64:
        write_token_from_base64(b64)


_bootstrap_secrets()


@dataclass
class AppConfig:
    """Shared sidebar settings, agreed on by every page via st.session_state."""

    save_symbols: tuple[str, ...]  # every symbol picked in the sidebar (>=1); Overview overlays all of them
    save_symbol: str               # save_symbols[0] -- used by the single-symbol pages (Drilldown/Strike Selector/Decision Screener) and the Expiry Richness table
    intent: Literal["buy", "sell"]
    target_delta: float
    delta_tolerance: float
    weights: ScoreWeights
    filters: ContractFilters


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


@st.cache_data(ttl=300, show_spinner="Loading latest snapshot...")
def _cached_snapshot(save_symbol: str, cache_bust: float) -> SnapshotBundle:
    return data_loader.load_latest_snapshot(save_symbol)


def get_snapshot(save_symbol: str) -> SnapshotBundle:
    """Cached snapshot load, keyed on (save_symbol, latest chain mtime) so a
    same-day manual refresh (CSVStore overwrites same-day files) still busts
    the cache. Lets FileNotFoundError propagate to the caller -- there is no
    snapshot at all yet, which is the "first run" case the UI must handle."""
    cache_bust = data_loader.latest_chain_mtime(save_symbol)
    return _cached_snapshot(save_symbol, cache_bust)


def load_snapshot_safely(save_symbol: str) -> SnapshotBundle | None:
    """UI-friendly wrapper around get_snapshot(): renders a friendly message
    (instead of a raw traceback) and returns None when there's no data yet
    or something else goes wrong loading it."""
    try:
        return get_snapshot(save_symbol)
    except FileNotFoundError:
        st.warning(
            f"No chain snapshot found yet for symbol **{save_symbol}**. "
            f"Run the data pipeline first from a terminal:\n\n"
            f"`python -m src.job` "
            f"(use `python -m src.job --first-time` instead if you haven't "
            f"authenticated with Schwab yet)."
        )
        return None
    except Exception as exc:  # defensive: surface a message, not a traceback
        st.error(f"Failed to load snapshot data: {exc}")
        return None


def _filter_metrics_by_dte(
    metrics: dict[str, pd.DataFrame], dte_range: tuple[int, int]
) -> dict[str, pd.DataFrame]:
    """Restrict every per-expiry metric DataFrame to the sidebar's DTE range.

    Overview's charts/table previously ignored the "Contract Filters" DTE
    range entirely (it was only ever read by the Decision Screener page for
    contract-level scoring), so narrowing it had no visible effect there --
    this makes every page respect the same control. richness_label/skew_bias
    z-scores in score_expiries() are computed *after* this filter runs, so
    "rich/cheap relative to other expiries on offer" means relative to the
    expiries actually in view, not the full 2-year window.
    """
    lo, hi = dte_range
    filtered: dict[str, pd.DataFrame] = {}
    for name, df in metrics.items():
        if df is not None and not df.empty and "dte" in df.columns:
            filtered[name] = df[(df["dte"] >= lo) & (df["dte"] <= hi)]
        else:
            filtered[name] = df
    return filtered


def get_expiry_scores(metrics: dict[str, pd.DataFrame]) -> pd.DataFrame | None:
    """Wraps decision_engine.score_expiries() with a friendly fallback when
    the required 'term_structure' metric is missing (score_expiries raises
    ValueError in that case) instead of letting the page crash."""
    if "term_structure" not in metrics or metrics["term_structure"] is None or metrics["term_structure"].empty:
        st.info("No term-structure metric available for this snapshot; expiry-level scoring is unavailable.")
        return None
    try:
        return decision_engine.score_expiries(metrics)
    except ValueError as exc:
        st.warning(f"Could not compute expiry scores: {exc}")
        return None


# ---------------------------------------------------------------------------
# Shared sidebar
# ---------------------------------------------------------------------------


def render_sidebar() -> AppConfig:
    """Renders every shared control in the sidebar and returns the resulting
    AppConfig. Widgets use explicit `key=` values so st.session_state keeps
    them in sync across page navigation (Streamlit multipage apps share one
    session_state across pages)."""
    st.sidebar.header("Dashboard Settings")

    save_symbols_selected = st.sidebar.multiselect(
        "Symbols",
        options=list(SYMBOL_REGISTRY.keys()),
        default=[DEFAULT_SAVE_SYMBOL],
        key="cfg_save_symbols",
        help="Pick one symbol for the normal single-symbol view, or several to "
        "overlay them on the Overview charts. The first symbol picked is used "
        "for the Expiry Drilldown / Strike Selector / Decision Screener pages "
        "and the Expiry Richness table.",
    )
    save_symbols = tuple(save_symbols_selected) or (DEFAULT_SAVE_SYMBOL,)
    save_symbol = save_symbols[0]

    st.sidebar.caption(f"Saved snapshot dates for {save_symbol} (informational only)")
    try:
        snap_dates = data_loader.list_snapshot_dates(save_symbol)
    except Exception:
        snap_dates = []
    if snap_dates:
        st.sidebar.text(", ".join(d.isoformat() for d in snap_dates[-8:]))
        st.sidebar.caption("Data always loads from the most recent snapshot above.")
    else:
        st.sidebar.text("No snapshots saved yet.")

    if st.sidebar.button("Refresh Live Data", use_container_width=True):
        succeeded: list[str] = []
        failed: list[tuple[str, str]] = []
        with st.spinner(f"Fetching live data from Schwab for {', '.join(save_symbols)}..."):
            for sym in save_symbols:
                info = SYMBOL_REGISTRY.get(sym)
                if info is None:
                    failed.append((sym, "not in the known symbol registry"))
                    continue
                try:
                    data_loader.trigger_live_refresh(
                        api_symbol=info.api_symbol,
                        save_symbol=sym,
                        strike_increment=info.strike_increment,
                    )
                    succeeded.append(sym)
                except FileNotFoundError as exc:
                    failed.append((
                        sym,
                        f"{exc} If this is your first time running the dashboard, set up "
                        f"authentication first: `python -m src.job --first-time`.",
                    ))
                except Exception as exc:
                    failed.append((
                        sym,
                        f"{exc} If this looks like a missing token/config issue, run "
                        f"`python -m src.job --first-time` from a terminal.",
                    ))
        for sym, msg in failed:
            st.sidebar.error(f"**{sym}** live refresh failed: {msg}")
        if succeeded:
            st.cache_data.clear()
            st.sidebar.success(f"Refreshed: {', '.join(succeeded)}.")
            st.rerun()

    st.sidebar.divider()
    st.sidebar.subheader("Trade Intent")
    intent_label = st.sidebar.radio(
        "Intent", ["Buy", "Sell"], index=0, key="cfg_intent_label", horizontal=True
    )
    intent: Literal["buy", "sell"] = "buy" if intent_label == "Buy" else "sell"

    target_delta = st.sidebar.slider(
        "Target delta", min_value=0.05, max_value=0.50, value=0.25, step=0.01, key="cfg_target_delta"
    )
    delta_tolerance = st.sidebar.slider(
        "Delta tolerance", min_value=0.05, max_value=0.30, value=0.15, step=0.01, key="cfg_delta_tolerance"
    )

    st.sidebar.divider()
    st.sidebar.subheader("Score Weights")
    w_value = st.sidebar.slider("Value weight", 0.0, 1.0, 0.4, step=0.05, key="cfg_w_value")
    w_delta_fit = st.sidebar.slider("Delta-fit weight", 0.0, 1.0, 0.3, step=0.05, key="cfg_w_delta_fit")
    w_liquidity = st.sidebar.slider("Liquidity weight", 0.0, 1.0, 0.3, step=0.05, key="cfg_w_liquidity")
    weights = ScoreWeights(value=w_value, delta_fit=w_delta_fit, liquidity=w_liquidity)

    st.sidebar.divider()
    st.sidebar.subheader("Contract Filters")
    dte_range = st.sidebar.slider("DTE range", 0, 730, (0, 730), key="cfg_dte_range")
    option_types = st.sidebar.multiselect(
        "Option types", ["CALL", "PUT"], default=["CALL", "PUT"], key="cfg_option_types"
    )
    min_volume = st.sidebar.number_input("Min volume", min_value=0, value=0, step=1, key="cfg_min_volume")
    min_oi = st.sidebar.number_input("Min open interest", min_value=0, value=0, step=1, key="cfg_min_oi")
    max_spread_pct = st.sidebar.slider(
        "Max spread %", min_value=0.0, max_value=100.0, value=25.0, step=1.0, key="cfg_max_spread_pct"
    )
    filters = ContractFilters(
        dte_range=(int(dte_range[0]), int(dte_range[1])),
        option_types=tuple(option_types) if option_types else ("CALL", "PUT"),
        min_volume=int(min_volume),
        min_open_interest=int(min_oi),
        max_spread_pct=float(max_spread_pct),
    )

    return AppConfig(
        save_symbols=save_symbols,
        save_symbol=save_symbol,
        intent=intent,
        target_delta=float(target_delta),
        delta_tolerance=float(delta_tolerance),
        weights=weights,
        filters=filters,
    )


# ---------------------------------------------------------------------------
# Overview page content
# ---------------------------------------------------------------------------


def render_overview(config: AppConfig) -> None:
    st.title("Options Vol Dashboard — Overview")

    bundles: dict[str, SnapshotBundle] = {}
    for sym in config.save_symbols:
        bundle = load_snapshot_safely(sym)
        if bundle is not None:
            bundles[sym] = bundle
    if not bundles:
        return

    primary = config.save_symbol
    as_of_source = primary if primary in bundles else next(iter(bundles))
    symbol_note = f"  ·  {len(bundles)}/{len(config.save_symbols)} symbols loaded" if len(config.save_symbols) > 1 else ""
    st.caption(
        f"As of {bundles[as_of_source].as_of}  ·  showing expiries "
        f"{config.filters.dte_range[0]}–{config.filters.dte_range[1]} DTE{symbol_note}"
    )

    filtered_metrics: dict[str, dict[str, pd.DataFrame]] = {
        sym: _filter_metrics_by_dte(bundle.metrics, config.filters.dte_range)
        for sym, bundle in bundles.items()
    }

    if len(config.save_symbols) == 1:
        # Single symbol: keep the original per-symbol chart titles/no-legend
        # layout rather than the overlay variant's generic titles + legend.
        metrics = filtered_metrics.get(primary, {})
        term_df = metrics.get("term_structure", pd.DataFrame())
        skew_df = metrics.get("skew", pd.DataFrame())
        curvature_df = metrics.get("curvature", pd.DataFrame())

        col1, col2 = st.columns(2)
        with col1:
            st.plotly_chart(
                chart_components.term_structure_chart(term_df, symbol=primary),
                use_container_width=True,
            )
        with col2:
            st.plotly_chart(
                chart_components.skew_chart(skew_df, symbol=primary),
                use_container_width=True,
            )

        col3, col4 = st.columns(2)
        with col3:
            st.plotly_chart(
                chart_components.curvature_chart(curvature_df, symbol=primary),
                use_container_width=True,
            )
        with col4:
            if "vrp" in metrics:
                st.plotly_chart(
                    chart_components.vrp_chart(metrics["vrp"], symbol=primary),
                    use_container_width=True,
                )
            else:
                st.info("VRP metric unavailable for this snapshot (needs price history at save time).")
    else:
        colors = {sym: SYMBOL_REGISTRY[sym].color for sym in config.save_symbols if sym in SYMBOL_REGISTRY}
        term_data = {sym: m.get("term_structure", pd.DataFrame()) for sym, m in filtered_metrics.items()}
        skew_data = {sym: m.get("skew", pd.DataFrame()) for sym, m in filtered_metrics.items()}
        curvature_data = {sym: m.get("curvature", pd.DataFrame()) for sym, m in filtered_metrics.items()}
        vrp_data = {sym: m["vrp"] for sym, m in filtered_metrics.items() if "vrp" in m}

        col1, col2 = st.columns(2)
        with col1:
            st.plotly_chart(
                chart_components.term_structure_chart_multi(term_data, colors),
                use_container_width=True,
            )
        with col2:
            st.plotly_chart(
                chart_components.skew_chart_multi(skew_data, colors),
                use_container_width=True,
            )

        col3, col4 = st.columns(2)
        with col3:
            st.plotly_chart(
                chart_components.curvature_chart_multi(curvature_data, colors),
                use_container_width=True,
            )
        with col4:
            if vrp_data:
                st.plotly_chart(
                    chart_components.vrp_chart_multi(vrp_data, colors),
                    use_container_width=True,
                )
            else:
                st.info("VRP metric unavailable for the selected symbols (needs price history at save time).")

    richness_heading = "Expiry Richness"
    if len(config.save_symbols) > 1:
        richness_heading += f" — {primary} (primary symbol; not blended across the comparison set)"
    st.subheader(richness_heading)

    if primary not in filtered_metrics:
        st.info(f"No data loaded for {primary} yet — click Refresh Live Data or run the pipeline for it.")
        return

    expiry_scores = get_expiry_scores(filtered_metrics[primary])
    if expiry_scores is not None:
        st.plotly_chart(
            chart_components.expiry_richness_table_style(expiry_scores),
            use_container_width=True,
        )
        st.caption(
            "**IV Richness** compares this expiry's implied vol to its own realized "
            "vol, ranked against the other expiries currently in view — *Rich* means "
            "options here are priced expensive (favors selling premium), *Cheap* "
            "means priced inexpensive (favors buying). **Put/Call Skew** is a "
            "separate signal: within this expiry's smile, which side (puts or "
            "calls) is priced richer relative to the other — it says nothing about "
            "whether the expiry as a whole is rich or cheap."
        )


def main() -> None:
    st.set_page_config(page_title="Options Vol Dashboard", layout="wide")
    config = render_sidebar()
    render_overview(config)


if __name__ == "__main__":
    main()
