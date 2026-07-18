"""
src/dashboard/pages/3_Decision_Screener.py

The "give me the answer" page: score_contracts() across the whole filtered
chain (all expiries, both sides), using only the shared sidebar settings --
no expiry pre-selection. Shows expiry-level context, the top N candidates
as a bar chart, the full scored/filtered table, and the smile context for
the #1 ranked contract.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd
import streamlit as st

from src.dashboard import chart_components, decision_engine
from src.dashboard.app import get_expiry_scores, load_snapshot_safely, render_sidebar

st.set_page_config(page_title="Decision Screener", layout="wide")

config = render_sidebar()

st.title("Decision Screener")

bundle = load_snapshot_safely(config.save_symbol)
if bundle is None:
    st.stop()

chain = bundle.chain
if chain is None or chain.empty:
    st.warning("No option chain data available for this snapshot.")
    st.stop()

# ---------------------------------------------------------------------------
# Expiry context
# ---------------------------------------------------------------------------

st.subheader("Expiry Context")
expiry_scores = get_expiry_scores(bundle.metrics)
if expiry_scores is not None:
    intent_richness = "Cheap" if config.intent == "buy" else "Rich"
    only_matching = st.checkbox(
        f"Only show expiries matching my intent ({intent_richness})",
        value=False,
        key="screener_only_matching",
    )
    display_scores = expiry_scores
    if only_matching:
        display_scores = expiry_scores[expiry_scores["richness_label"] == intent_richness]
        if display_scores.empty:
            st.info(f"No expiries are currently labeled '{intent_richness}'.")
    st.plotly_chart(
        chart_components.expiry_richness_table_style(display_scores),
        use_container_width=True,
    )

# ---------------------------------------------------------------------------
# Contract-level screen across the whole chain
# ---------------------------------------------------------------------------

st.subheader("Top Candidates")
n_candidates = st.slider(
    "Number of candidates", min_value=1, max_value=50, value=10, key="screener_n_candidates"
)

scored = decision_engine.score_contracts(
    chain,
    intent=config.intent,
    target_delta=config.target_delta,
    delta_tolerance=config.delta_tolerance,
    weights=config.weights,
    filters=config.filters,
)

if scored.empty:
    st.info("No contracts match the current filters.")
    st.stop()

top_df = decision_engine.top_candidates(scored, n=n_candidates)

st.plotly_chart(chart_components.candidate_score_bar(top_df), use_container_width=True)

st.subheader("Full Detail Table")
st.caption(
    f"{len(scored)} contract(s) match your current filters across all expiries. "
    f"Columns are sortable by clicking their header."
)
display_cols = [
    c
    for c in [
        "rank",
        "expiration",
        "dte",
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
st.dataframe(scored[display_cols], use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# Smile context for the #1 ranked contract
# ---------------------------------------------------------------------------

st.subheader("Top Candidate — Smile Context")
top_row = scored.iloc[0]
top_expiration = top_row["expiration"]
expiry_chain = chain[chain["expiration"] == top_expiration]

exp_label = top_expiration.date().isoformat() if hasattr(top_expiration, "date") else str(top_expiration)
score_suffix = (
    f" (composite score {top_row['composite_score']:.1f})"
    if pd.notna(top_row.get("composite_score"))
    else ""
)
st.caption(f"#1: {top_row['optionType']} {top_row['strikePrice']:g} exp {exp_label}{score_suffix}")
st.plotly_chart(
    chart_components.smile_chart(expiry_chain, highlight_row=top_row),
    use_container_width=True,
)
