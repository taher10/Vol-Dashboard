"""
src/dashboard/pages/2_Strike_Selector.py

Pick an expiry + option type, see every surviving contract scored and
ranked (decision_engine.score_contracts scoped to that expiry via
ContractFilters(dte_range=(dte, dte), ...)), pick one row, and see it
highlighted on that expiry's volatility smile.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from src.dashboard import chart_components, decision_engine
from src.dashboard.app import load_snapshot_safely, render_sidebar
from src.dashboard.decision_engine import ContractFilters

st.set_page_config(page_title="Strike Selector", layout="wide")

config = render_sidebar()

st.title("Strike Selector")

bundle = load_snapshot_safely(config.save_symbol)
if bundle is None:
    st.stop()

chain = bundle.chain
if chain is None or chain.empty or "expiration" not in chain.columns:
    st.warning("No option chain data available for this snapshot.")
    st.stop()

expiry_dte = (
    chain[["expiration", "dte"]]
    .dropna()
    .drop_duplicates()
    .sort_values("dte")
    .reset_index(drop=True)
)
if expiry_dte.empty:
    st.warning("No expirations found in the chain.")
    st.stop()


def _fmt_label(expiration, dte) -> str:
    exp_str = expiration.date().isoformat() if hasattr(expiration, "date") else str(expiration)
    return f"{exp_str} (DTE {int(dte)})"


expiry_labels = [_fmt_label(r.expiration, r.dte) for r in expiry_dte.itertuples()]
label_to_exp = dict(zip(expiry_labels, expiry_dte["expiration"]))
label_to_dte = dict(zip(expiry_labels, expiry_dte["dte"]))

col_a, col_b = st.columns([2, 1])
with col_a:
    selected_label = st.selectbox("Expiry", expiry_labels, key="strike_sel_expiry")
with col_b:
    type_choice = st.selectbox("Option type", ["Both", "Call", "Put"], key="strike_sel_type")

selected_expiration = label_to_exp[selected_label]
selected_dte = int(label_to_dte[selected_label])

if type_choice == "Call":
    option_types: tuple[str, ...] = ("CALL",)
elif type_choice == "Put":
    option_types = ("PUT",)
else:
    option_types = ("CALL", "PUT")

# Scope to just this expiry via an inclusive single-value dte_range, but
# keep the shared sidebar's liquidity/spread filters so this page stays
# consistent with the rest of the app.
page_filters = ContractFilters(
    dte_range=(selected_dte, selected_dte),
    option_types=option_types,
    min_volume=config.filters.min_volume,
    min_open_interest=config.filters.min_open_interest,
    max_spread_pct=config.filters.max_spread_pct,
)

scored = decision_engine.score_contracts(
    chain,
    intent=config.intent,
    target_delta=config.target_delta,
    delta_tolerance=config.delta_tolerance,
    weights=config.weights,
    filters=page_filters,
)

if scored.empty:
    st.info("No contracts match the current filters for this expiry.")
    st.stop()

display_cols = [
    c
    for c in [
        "rank",
        "optionType",
        "strikePrice",
        "bid",
        "ask",
        "mid",
        "spread_pct",
        "volume",
        "openInterest",
        "delta",
        "impliedVolatility",
        "liquidity_score",
        "delta_fit_score",
        "value_score",
        "composite_score",
    ]
    if c in scored.columns
]

st.subheader(f"Scored Contracts — {selected_label}")
st.caption("Click a row to highlight it on the smile chart below. Columns are sortable by clicking their header.")

event = st.dataframe(
    scored[display_cols],
    use_container_width=True,
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
    key="strike_selector_table",
)

selected_rows = list(event.selection.rows) if event is not None else []
if selected_rows:
    highlight = scored.iloc[selected_rows[0]]
    highlight_note = ""
else:
    highlight = scored.iloc[0]
    highlight_note = " (top-ranked by default — click a row above to change)"

st.caption(
    f"Highlighting: {highlight['optionType']} {highlight['strikePrice']:g} "
    f"(rank {int(highlight['rank'])}){highlight_note}"
)

expiry_chain = chain[chain["expiration"] == selected_expiration]
st.plotly_chart(
    chart_components.smile_chart(expiry_chain, highlight_row=highlight),
    use_container_width=True,
)
