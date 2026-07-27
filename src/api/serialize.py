"""
src/api/serialize.py

DataFrame -> JSON-safe conversion helpers shared by every route. Centralized
here so NaN/NaT/numpy-scalar handling only needs to be gotten right once —
FastAPI's default JSON encoder chokes on NaN/Infinity (not valid JSON) and
on raw numpy/pandas scalar types.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def clean_value(value: Any) -> Any:
    """Convert one pandas/numpy scalar into a JSON-safe Python value."""
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.isoformat()
    if isinstance(value, pd.Timedelta):
        return None if pd.isna(value) else value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        f = float(value)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.ndarray,)):
        return [clean_value(v) for v in value.tolist()]
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def df_records(df: pd.DataFrame | None) -> list[dict]:
    """Convert a DataFrame into a list of JSON-safe records, empty list for None/empty input."""
    if df is None or df.empty:
        return []
    return [
        {k: clean_value(v) for k, v in row.items()}
        for row in df.to_dict(orient="records")
    ]


def series_record(series: "pd.Series | dict | None") -> dict | None:
    """Convert a single Series/dict (e.g. one scored-expiry row) into a JSON-safe dict."""
    if series is None:
        return None
    items = series.items() if isinstance(series, pd.Series) else series.items()
    return {k: clean_value(v) for k, v in items}
