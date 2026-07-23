"""
src/dashboard/chart_components.py

Pure DataFrame -> plotly.graph_objects.Figure builders for the interactive
Streamlit dashboard. Mirrors src/charts.py's MetricsChartBuilder (one function
per chart) but targets plotly.graph_objects instead of matplotlib, and returns
Figures rather than saving PNGs. No streamlit import here — rendering is the
caller's job.

Color usage follows a fixed, reused palette so calls/puts and rich/cheap read
consistently everywhere they appear:
    - CALL series: blue  (#2a78d6)
    - PUT  series: red   (#e34948)
    - Rich (status "bad" for premium buyers): red tint
    - Cheap (status "good"):                  green tint
    - Neutral:                                 gray tint
These two categorical hues (blue/red) and the status trio pass the CVD /
contrast checks used across the app; see the dataviz color-formula notes.
"""

from __future__ import annotations

from typing import Mapping

import pandas as pd
import plotly.graph_objects as go

# ---------------------------------------------------------------------------
# Shared style constants
# ---------------------------------------------------------------------------

TEMPLATE = "plotly_white"

COLOR_CALL = "#2a78d6"
COLOR_PUT = "#e34948"
COLOR_LINE_DEFAULT = "#2a78d6"
COLOR_ZERO_LINE = "#898781"
COLOR_GRID = "#e1e0d9"
COLOR_TEXT_PRIMARY = "#0b0b0b"
COLOR_TEXT_MUTED = "#898781"

COLOR_HIGHLIGHT = "#0b0b0b"

# Status trio (reserved meaning; not reused for generic series).
STATUS_GOOD = "#0ca30c"     # "Cheap"
STATUS_CRITICAL = "#d03b3b"  # "Rich"
STATUS_NEUTRAL = "#898781"   # "Neutral"

RICHNESS_BG = {
    "cheap": "rgba(12, 163, 12, 0.16)",
    "rich": "rgba(208, 59, 59, 0.16)",
    "neutral": "rgba(137, 135, 129, 0.14)",
}
RICHNESS_TEXT = {
    "cheap": "#0b5c0b",
    "rich": "#8f2323",
    "neutral": COLOR_TEXT_MUTED,
}

ROW_BG_A = "#fcfcfb"
ROW_BG_B = "#f4f4f2"
ROW_BG_DEEMPHASIS = "#e6e5e1"
ROW_TEXT_DEEMPHASIS = COLOR_TEXT_MUTED


def _empty_figure(title: str, message: str = "No data available") -> go.Figure:
    """Valid, empty Figure with a centered annotation — used for empty/NaN input."""
    fig = go.Figure()
    fig.update_layout(
        template=TEMPLATE,
        title=title,
        xaxis={"visible": False},
        yaxis={"visible": False},
        annotations=[
            {
                "text": message,
                "xref": "paper",
                "yref": "paper",
                "x": 0.5,
                "y": 0.5,
                "showarrow": False,
                "font": {"size": 16, "color": COLOR_TEXT_MUTED},
            }
        ],
    )
    return fig


def _has_usable_data(df: pd.DataFrame | None, cols: list[str]) -> bool:
    if df is None or df.empty:
        return False
    if not all(c in df.columns for c in cols):
        return False
    return not df[cols].dropna(how="any").empty


def _line_metric_chart(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    title: str,
    y_title: str,
    color: str,
    zero_line: bool = False,
    dte_col: str = "dte",
) -> go.Figure:
    """Shared builder behind term_structure/skew/curvature/vrp — same mark spec
    (2px line, marker, optional zero baseline) with each public function only
    varying title/column/color, matching MetricsChartBuilder's one-chart-per-
    metric shape without repeating the plumbing four times.

    x_col is the expiration date (not DTE) so the axis reads in calendar
    terms -- traders think "the September expiry", not "DTE 57" -- with
    quarterly tick labels since the term structure spans up to two years.
    DTE is kept one click away via customdata in the hover tooltip rather
    than dropped, since it's still the more precise value for comparing
    expiries.
    """
    if not _has_usable_data(df, [x_col, y_col]):
        return _empty_figure(title)

    has_dte = dte_col in df.columns
    cols = [x_col, y_col] + ([dte_col] if has_dte else [])
    plot_df = df[cols].dropna(subset=[x_col, y_col]).sort_values(x_col)

    hovertemplate = "%{x|%b %d, %Y}"
    if has_dte:
        hovertemplate += "<br>DTE %{customdata}"
    hovertemplate += f"<br>{y_title} %{{y:.3f}}<extra></extra>"

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=plot_df[x_col],
            y=plot_df[y_col],
            mode="lines+markers",
            line={"width": 2, "color": color},
            marker={"size": 8, "color": color, "line": {"width": 2, "color": "#fcfcfb"}},
            name=y_title,
            customdata=plot_df[dte_col] if has_dte else None,
            hovertemplate=hovertemplate,
        )
    )
    if zero_line:
        fig.add_hline(y=0.0, line_width=1, line_color=COLOR_ZERO_LINE)

    fig.update_layout(
        template=TEMPLATE,
        title=title,
        xaxis_title="Expiration",
        yaxis_title=y_title,
        showlegend=False,
        margin={"t": 60, "b": 40, "l": 60, "r": 30},
    )
    fig.update_xaxes(gridcolor=COLOR_GRID, zeroline=False, dtick="M3", tickformat="%b '%y")
    fig.update_yaxes(gridcolor=COLOR_GRID, zeroline=False)
    return fig


# ---------------------------------------------------------------------------
# Public chart functions
# ---------------------------------------------------------------------------

def term_structure_chart(df: pd.DataFrame, symbol: str = "Underlying") -> go.Figure:
    """Line+marker chart: x=expiration (quarterly ticks), y=atm_iv."""
    return _line_metric_chart(
        df,
        x_col="expiration",
        y_col="atm_iv",
        title=f"{symbol} ATM IV Term Structure",
        y_title="ATM IV (%)",
        color=COLOR_LINE_DEFAULT,
    )


def skew_chart(df: pd.DataFrame, symbol: str = "Underlying") -> go.Figure:
    """Line+marker chart: x=expiration (quarterly ticks), y=skew, with a horizontal zero reference line."""
    return _line_metric_chart(
        df,
        x_col="expiration",
        y_col="skew",
        title=f"{symbol} 25D Put-Call Skew",
        y_title="Skew (IV points)",
        color=COLOR_PUT,
        zero_line=True,
    )


def curvature_chart(df: pd.DataFrame, symbol: str = "Underlying") -> go.Figure:
    """Line+marker chart: x=expiration (quarterly ticks), y=curvature, with a horizontal zero reference line."""
    return _line_metric_chart(
        df,
        x_col="expiration",
        y_col="curvature",
        title=f"{symbol} Smile Curvature",
        y_title="Curvature (IV points)",
        color="#4a3aa7",
        zero_line=True,
    )


def vrp_chart(df: pd.DataFrame, symbol: str = "Underlying") -> go.Figure:
    """Line+marker chart: x=expiration (quarterly ticks), y=vrp, with a horizontal zero reference line."""
    return _line_metric_chart(
        df,
        x_col="expiration",
        y_col="vrp",
        title=f"{symbol} Volatility Risk Premium",
        y_title="VRP (IV - RV)",
        color=STATUS_GOOD,
        zero_line=True,
    )


# ---------------------------------------------------------------------------
# Multi-symbol overlay chart functions
# ---------------------------------------------------------------------------


def _multi_line_metric_chart(
    data: Mapping[str, pd.DataFrame],
    colors: Mapping[str, str],
    x_col: str,
    y_col: str,
    title: str,
    y_title: str,
    zero_line: bool = False,
    dte_col: str = "dte",
) -> go.Figure:
    """Overlay one line per symbol on shared axes -- same mark spec as
    _line_metric_chart (2px line, 8px marker, quarterly date ticks) but with
    a legend, since with >=2 series identity can no longer be carried by
    the title alone. `colors` should assign each symbol a fixed hue by
    identity (SYMBOL_REGISTRY in app.py), not by draw order, so a symbol's
    line color stays constant as the selection changes. Symbols with no
    usable data for this particular metric are skipped rather than erroring
    the whole chart, so e.g. one symbol's snapshot missing VRP (no price
    history at save time) doesn't blank out the others.
    """
    usable = {sym: df for sym, df in data.items() if _has_usable_data(df, [x_col, y_col])}
    if not usable:
        return _empty_figure(title)

    fig = go.Figure()
    for sym, df in usable.items():
        has_dte = dte_col in df.columns
        cols = [x_col, y_col] + ([dte_col] if has_dte else [])
        plot_df = df[cols].dropna(subset=[x_col, y_col]).sort_values(x_col)
        color = colors.get(sym, COLOR_TEXT_MUTED)

        hovertemplate = f"<b>{sym}</b><br>%{{x|%b %d, %Y}}"
        if has_dte:
            hovertemplate += "<br>DTE %{customdata}"
        hovertemplate += f"<br>{y_title} %{{y:.3f}}<extra></extra>"

        fig.add_trace(
            go.Scatter(
                x=plot_df[x_col],
                y=plot_df[y_col],
                mode="lines+markers",
                name=sym,
                line={"width": 2, "color": color},
                marker={"size": 8, "color": color, "line": {"width": 2, "color": "#fcfcfb"}},
                customdata=plot_df[dte_col] if has_dte else None,
                hovertemplate=hovertemplate,
            )
        )

    if zero_line:
        fig.add_hline(y=0.0, line_width=1, line_color=COLOR_ZERO_LINE)

    fig.update_layout(
        template=TEMPLATE,
        title=title,
        xaxis_title="Expiration",
        yaxis_title=y_title,
        showlegend=True,
        legend={"orientation": "h", "y": 1.14, "x": 0},
        margin={"t": 80, "b": 40, "l": 60, "r": 30},
    )
    fig.update_xaxes(gridcolor=COLOR_GRID, zeroline=False, dtick="M3", tickformat="%b '%y")
    fig.update_yaxes(gridcolor=COLOR_GRID, zeroline=False)
    return fig


def term_structure_chart_multi(data: Mapping[str, pd.DataFrame], colors: Mapping[str, str]) -> go.Figure:
    """Overlaid ATM IV term structure, one line per symbol."""
    return _multi_line_metric_chart(
        data, colors, x_col="expiration", y_col="atm_iv",
        title="ATM IV Term Structure", y_title="ATM IV (%)",
    )


def skew_chart_multi(data: Mapping[str, pd.DataFrame], colors: Mapping[str, str]) -> go.Figure:
    """Overlaid 25D put-call skew, one line per symbol."""
    return _multi_line_metric_chart(
        data, colors, x_col="expiration", y_col="skew",
        title="25D Put-Call Skew", y_title="Skew (IV points)", zero_line=True,
    )


def curvature_chart_multi(data: Mapping[str, pd.DataFrame], colors: Mapping[str, str]) -> go.Figure:
    """Overlaid smile curvature, one line per symbol."""
    return _multi_line_metric_chart(
        data, colors, x_col="expiration", y_col="curvature",
        title="Smile Curvature", y_title="Curvature (IV points)", zero_line=True,
    )


def vrp_chart_multi(data: Mapping[str, pd.DataFrame], colors: Mapping[str, str]) -> go.Figure:
    """Overlaid volatility risk premium, one line per symbol."""
    return _multi_line_metric_chart(
        data, colors, x_col="expiration", y_col="vrp",
        title="Volatility Risk Premium", y_title="VRP (IV - RV)", zero_line=True,
    )


def smile_chart(
    chain_df_for_expiry: pd.DataFrame,
    highlight_row: "pd.Series | Mapping | None" = None,
) -> go.Figure:
    """
    Volatility smile for one expiry. x is signed delta (puts -> -abs(delta),
    calls -> abs(delta)) so the smile reads left (deep puts) to right (deep
    calls) like a standard skew/smile plot; CALL and PUT are separate traces
    so identity never depends on color alone (legend + distinct symbols).
    """
    title = "Volatility Smile"
    required = ["optionType", "delta", "impliedVolatility"]
    if not _has_usable_data(chain_df_for_expiry, required):
        return _empty_figure(title)

    df = chain_df_for_expiry.dropna(subset=required).copy()
    df["_signed_delta"] = df.apply(
        lambda r: -abs(r["delta"]) if str(r["optionType"]).upper() == "PUT" else abs(r["delta"]),
        axis=1,
    )

    expiration = None
    if "expiration" in df.columns and not df["expiration"].empty:
        expiration = df["expiration"].iloc[0]
    if expiration:
        title = f"Volatility Smile — {expiration}"

    fig = go.Figure()

    calls = df[df["optionType"].astype(str).str.upper() == "CALL"].sort_values("_signed_delta")
    puts = df[df["optionType"].astype(str).str.upper() == "PUT"].sort_values("_signed_delta")

    if not puts.empty:
        fig.add_trace(
            go.Scatter(
                x=puts["_signed_delta"],
                y=puts["impliedVolatility"],
                mode="lines+markers",
                name="PUT",
                line={"width": 2, "color": COLOR_PUT},
                marker={"size": 8, "color": COLOR_PUT, "line": {"width": 2, "color": "#fcfcfb"}},
                customdata=puts.get("strikePrice", pd.Series(dtype=float)),
                hovertemplate="PUT strike %{customdata}<br>delta %{x:.3f}<br>IV %{y:.3f}<extra></extra>",
            )
        )
    if not calls.empty:
        fig.add_trace(
            go.Scatter(
                x=calls["_signed_delta"],
                y=calls["impliedVolatility"],
                mode="lines+markers",
                name="CALL",
                line={"width": 2, "color": COLOR_CALL},
                marker={"size": 8, "color": COLOR_CALL, "line": {"width": 2, "color": "#fcfcfb"}},
                customdata=calls.get("strikePrice", pd.Series(dtype=float)),
                hovertemplate="CALL strike %{customdata}<br>delta %{x:.3f}<br>IV %{y:.3f}<extra></extra>",
            )
        )

    if highlight_row is not None:
        hr = dict(highlight_row)
        h_delta = hr.get("delta")
        h_iv = hr.get("impliedVolatility")
        h_type = str(hr.get("optionType", "")).upper()
        if h_delta is not None and h_iv is not None:
            signed = -abs(h_delta) if h_type == "PUT" else abs(h_delta)
            strike = hr.get("strikePrice", "")
            fig.add_trace(
                go.Scatter(
                    x=[signed],
                    y=[h_iv],
                    mode="markers",
                    name="Selected",
                    marker={
                        "symbol": "star",
                        "size": 20,
                        "color": COLOR_HIGHLIGHT,
                        "line": {"width": 2, "color": "#fcfcfb"},
                    },
                    hovertemplate=f"Selected {h_type} strike {strike}<br>delta %{{x:.3f}}<br>IV %{{y:.3f}}<extra></extra>",
                )
            )

    fig.update_layout(
        template=TEMPLATE,
        title=title,
        xaxis_title="Delta (put ←  → call)",
        yaxis_title="Implied Volatility (%)",
        legend={"orientation": "h", "y": 1.08, "x": 0},
        margin={"t": 70, "b": 40, "l": 60, "r": 30},
    )
    fig.update_xaxes(gridcolor=COLOR_GRID, zeroline=True, zerolinecolor=COLOR_GRID)
    fig.update_yaxes(gridcolor=COLOR_GRID, zeroline=False)
    return fig


def expiry_richness_table_style(df: pd.DataFrame) -> go.Figure:
    """
    Expiry-level summary table (decision_engine.score_expiries output).
    richness_label cells get a status tint (green=Cheap/red=Rich/gray=Neutral);
    rows with has_wing_data == False are greyed and flagged so missing wing
    data is obvious rather than silently blank.
    """
    title = "Expiry Richness Summary"
    if df is None or df.empty:
        return _empty_figure(title)

    preferred_cols = [
        "expiration",
        "dte",
        "atm_iv",
        "vrp",
        "vrp_z",
        "richness_label",
        "skew_bias",
        "has_wing_data",
    ]
    cols = [c for c in preferred_cols if c in df.columns]
    if not cols:
        return _empty_figure(title)

    work = df[cols].copy().reset_index(drop=True)
    has_wing = (
        work["has_wing_data"].fillna(False).astype(bool)
        if "has_wing_data" in work.columns
        else pd.Series(True, index=work.index)
    )

    n = len(work)
    row_bg = [ROW_BG_A if i % 2 == 0 else ROW_BG_B for i in range(n)]
    row_bg = [ROW_BG_DEEMPHASIS if not has_wing.iloc[i] else row_bg[i] for i in range(n)]
    row_text = [ROW_TEXT_DEEMPHASIS if not has_wing.iloc[i] else COLOR_TEXT_PRIMARY for i in range(n)]

    def _fmt(col: str, val) -> str:
        if pd.isna(val):
            return "—"
        if col == "expiration":
            return str(val)
        if col == "dte":
            return f"{int(val)}"
        if col in ("atm_iv", "vrp", "vrp_z"):
            return f"{float(val):.3f}"
        if col == "has_wing_data":
            return "Yes" if bool(val) else "No wing data"
        return str(val)

    header_labels = {
        "expiration": "Expiration",
        "dte": "DTE",
        "atm_iv": "ATM IV",
        "vrp": "VRP (IV−RV)",
        "vrp_z": "VRP Z-score",
        "richness_label": "IV Richness",
        "skew_bias": "Put/Call Skew",
        "has_wing_data": "Wing Data",
    }

    cell_values: list[list[str]] = []
    cell_fill: list[list[str]] = []
    cell_font_color: list[list[str]] = []

    for col in cols:
        col_values = []
        col_fill = []
        col_font = []
        for i in range(n):
            raw = work.iloc[i][col]
            label_text = _fmt(col, raw)
            if col == "expiration" and not has_wing.iloc[i]:
                label_text = f"⚠ {label_text}"

            fill = row_bg[i]
            font_color = row_text[i]
            if col == "richness_label" and has_wing.iloc[i] and pd.notna(raw):
                key = str(raw).strip().lower()
                if key in RICHNESS_BG:
                    fill = RICHNESS_BG[key]
                    font_color = RICHNESS_TEXT[key]

            col_values.append(label_text)
            col_fill.append(fill)
            col_font.append(font_color)
        cell_values.append(col_values)
        cell_fill.append(col_fill)
        cell_font_color.append(col_font)

    fig = go.Figure(
        data=[
            go.Table(
                header={
                    "values": [header_labels.get(c, c) for c in cols],
                    "fill_color": "#0b0b0b",
                    "font": {"color": "#ffffff", "size": 13},
                    "align": "left",
                    "height": 32,
                },
                cells={
                    "values": cell_values,
                    "fill_color": cell_fill,
                    "font": {"color": cell_font_color, "size": 12},
                    "align": "left",
                    "height": 28,
                },
            )
        ]
    )
    fig.update_layout(template=TEMPLATE, title=title, margin={"t": 50, "b": 10, "l": 10, "r": 10})
    return fig


def candidate_score_bar(top_df: pd.DataFrame) -> go.Figure:
    """
    Horizontal bar of top-N scored candidates (decision_engine.top_candidates
    output), highest composite_score at top. Split into CALL/PUT traces (same
    colors as smile_chart) sharing one explicit category order so the legend
    carries identity instead of relying on a single mixed-color trace.
    """
    title = "Top Candidate Contracts"
    required = ["strikePrice", "optionType", "expiration", "composite_score"]
    if not _has_usable_data(top_df, required):
        return _empty_figure(title)

    work = top_df.dropna(subset=required).copy()
    work["_label"] = (
        work["optionType"].astype(str).str.upper()
        + " "
        + work["strikePrice"].apply(lambda v: f"{v:g}")
        + " "
        + work["expiration"].astype(str)
    )
    # Ascending so the highest score lands last -> plotly draws the last
    # category at the top of a horizontal bar chart.
    work = work.sort_values("composite_score", ascending=True)
    category_order = work["_label"].tolist()

    fig = go.Figure()
    for option_type, color in (("PUT", COLOR_PUT), ("CALL", COLOR_CALL)):
        subset = work[work["optionType"].astype(str).str.upper() == option_type]
        if subset.empty:
            continue
        fig.add_trace(
            go.Bar(
                x=subset["composite_score"],
                y=subset["_label"],
                orientation="h",
                name=option_type,
                marker={"color": color},
                text=[f"{v:.3f}" for v in subset["composite_score"]],
                textposition="outside",
                hovertemplate="%{y}<br>score %{x:.3f}<extra></extra>",
            )
        )

    fig.update_layout(
        template=TEMPLATE,
        title=title,
        xaxis_title="Composite Score",
        yaxis_title=None,
        yaxis={"categoryorder": "array", "categoryarray": category_order},
        legend={"orientation": "h", "y": 1.08, "x": 0},
        margin={"t": 70, "b": 40, "l": 160, "r": 40},
    )
    fig.update_xaxes(gridcolor=COLOR_GRID, zeroline=False)
    fig.update_yaxes(gridcolor=COLOR_GRID)
    return fig
