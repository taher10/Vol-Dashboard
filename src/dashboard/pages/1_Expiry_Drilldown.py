"""
src/dashboard/pages/1_Expiry_Drilldown.py

Pick one expiry and drill in: its volatility smile, its richness/skew/
curvature readout from score_expiries(), and how its ATM IV compares to
its two nearest neighbors on the term structure.
"""

from __future__ import annotations

import sys
from pathlib import Path

# This file lives at src/dashboard/pages/<name>.py -- four levels below the
# project root (pages -> dashboard -> src -> root), one deeper than app.py.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd
import streamlit as st

from src.dashboard import chart_components
from src.dashboard.app import get_expiry_scores, load_snapshot_safely, render_sidebar

st.set_page_config(page_title="Expiry Drilldown", layout="wide")

config = render_sidebar()

st.title("Expiry Drilldown")

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

selected_label = st.selectbox("Expiry", expiry_labels, key="drilldown_expiry")
selected_expiration = label_to_exp[selected_label]

expiry_chain = chain[chain["expiration"] == selected_expiration]

st.plotly_chart(chart_components.smile_chart(expiry_chain), use_container_width=True)

expiry_scores = get_expiry_scores(bundle.metrics)
if expiry_scores is not None:
    # score_expiries() already returns rows sorted ascending by dte, so
    # positional neighbors in this frame ARE term-structure neighbors.
    match_idx = expiry_scores.index[expiry_scores["expiration"] == selected_expiration].tolist()
    if not match_idx:
        st.info("No expiry-level score row found for this expiration.")
    else:
        idx = match_idx[0]
        row = expiry_scores.iloc[idx]

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("ATM IV", f"{row['atm_iv']:.3f}" if pd.notna(row.get("atm_iv")) else "—")
        c2.metric("Skew", f"{row['skew']:.3f}" if pd.notna(row.get("skew")) else "—")
        c3.metric("Curvature", f"{row['curvature']:.3f}" if pd.notna(row.get("curvature")) else "—")
        c4.metric("Richness", str(row.get("richness_label", "—")))
        st.caption(
            f"Skew bias: {row.get('skew_bias', '—')} | "
            f"Has wing data: {'Yes' if bool(row.get('has_wing_data', False)) else 'No'}"
        )

        st.subheader("ATM IV vs. Neighboring Expiries")
        neighbor_rows = []
        if idx > 0:
            neighbor_rows.append(("Previous", expiry_scores.iloc[idx - 1]))
        neighbor_rows.append(("Selected", row))
        if idx < len(expiry_scores) - 1:
            neighbor_rows.append(("Next", expiry_scores.iloc[idx + 1]))

        neighbor_df = pd.DataFrame(
            [
                {
                    "Position": pos,
                    "Expiration": n["expiration"].date() if hasattr(n["expiration"], "date") else n["expiration"],
                    "DTE": int(n["dte"]),
                    "ATM IV": n["atm_iv"],
                    "Richness": n.get("richness_label", "—"),
                }
                for pos, n in neighbor_rows
            ]
        )
        st.dataframe(neighbor_df, use_container_width=True, hide_index=True)

        if len(neighbor_rows) < 3:
            st.caption("This expiry is at an edge of the term structure, so it only has one neighbor.")
