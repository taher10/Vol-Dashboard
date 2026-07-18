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

from src.dashboard import chart_components, data_loader, decision_engine
from src.dashboard.data_loader import SnapshotBundle
from src.dashboard.decision_engine import ContractFilters, ScoreWeights

DEFAULT_SAVE_SYMBOL = "SPX"


@dataclass
class AppConfig:
    """Shared sidebar settings, agreed on by every page via st.session_state."""

    save_symbol: str
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

    save_symbol = (
        st.sidebar.text_input(
            "Save symbol",
            value=DEFAULT_SAVE_SYMBOL,
            key="cfg_save_symbol",
            help="Symbol snapshots are stored/loaded under (e.g. SPX).",
        )
        .strip()
        .upper()
        or DEFAULT_SAVE_SYMBOL
    )

    st.sidebar.caption("Saved snapshot dates (informational only)")
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
        api_symbol = save_symbol if save_symbol.startswith("$") else f"${save_symbol}"
        try:
            with st.spinner("Fetching live data from Schwab..."):
                data_loader.trigger_live_refresh(api_symbol=api_symbol, save_symbol=save_symbol)
        except FileNotFoundError as exc:
            st.sidebar.error(
                f"Live refresh failed: {exc}\n\n"
                f"If this is your first time running the dashboard, set up "
                f"authentication first: `python -m src.job --first-time`."
            )
        except Exception as exc:
            st.sidebar.error(
                f"Live refresh failed: {exc}\n\n"
                f"If this looks like a missing token/config issue, run "
                f"`python -m src.job --first-time` from a terminal."
            )
        else:
            st.cache_data.clear()
            st.sidebar.success("Refreshed.")
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

    bundle = load_snapshot_safely(config.save_symbol)
    if bundle is None:
        return

    st.caption(f"As of {bundle.as_of}")

    term_df = bundle.metrics.get("term_structure", pd.DataFrame())
    skew_df = bundle.metrics.get("skew", pd.DataFrame())
    curvature_df = bundle.metrics.get("curvature", pd.DataFrame())

    col1, col2 = st.columns(2)
    with col1:
        st.plotly_chart(
            chart_components.term_structure_chart(term_df, symbol=config.save_symbol),
            use_container_width=True,
        )
    with col2:
        st.plotly_chart(
            chart_components.skew_chart(skew_df, symbol=config.save_symbol),
            use_container_width=True,
        )

    col3, col4 = st.columns(2)
    with col3:
        st.plotly_chart(
            chart_components.curvature_chart(curvature_df, symbol=config.save_symbol),
            use_container_width=True,
        )
    with col4:
        if "vrp" in bundle.metrics:
            st.plotly_chart(
                chart_components.vrp_chart(bundle.metrics["vrp"], symbol=config.save_symbol),
                use_container_width=True,
            )
        else:
            st.info("VRP metric unavailable for this snapshot (needs price history at save time).")

    st.subheader("Expiry Richness")
    expiry_scores = get_expiry_scores(bundle.metrics)
    if expiry_scores is not None:
        st.plotly_chart(
            chart_components.expiry_richness_table_style(expiry_scores),
            use_container_width=True,
        )


def main() -> None:
    st.set_page_config(page_title="Options Vol Dashboard", layout="wide")
    config = render_sidebar()
    render_overview(config)


if __name__ == "__main__":
    main()
