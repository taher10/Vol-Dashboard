"""
src/debug_session.py

Pre-loads all latest CSV snapshots into named DataFrames for interactive
exploration in the VS Code debugger or Python REPL.

Usage
-----
Run this file with the debugger (F5 → "Debug Session").
Set a breakpoint on the last line (`pass`) — all DataFrames will be live
in the Variables panel and the Debug Console.

Or import in a REPL:
    from src.debug_session import *
    chain.head()
    skew
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd

from src.data_store import CSVStore

# ---------------------------------------------------------------------------
# Load all latest snapshots
# ---------------------------------------------------------------------------

store = CSVStore(symbol="SPX")

# Raw chain: one row per option contract
try:
    chain: pd.DataFrame = store.load_latest_chain()
    print(f"chain          : {chain.shape[0]:,} rows × {chain.shape[1]} cols")
except FileNotFoundError:
    chain = pd.DataFrame()
    print("chain          : not found (run job.py first)")

# Price history
try:
    prices: pd.DataFrame = store.load_latest_price_history()
    print(f"prices         : {len(prices):,} rows  ({prices['datetime'].iloc[0].date()} → {prices['datetime'].iloc[-1].date()})")
except FileNotFoundError:
    prices = pd.DataFrame()
    print("prices         : not found")

# Computed metrics
_METRICS = ["term_structure", "skew", "skew_ratio", "curvature", "vrp"]
_metric_frames: dict[str, pd.DataFrame] = {}

for _name in _METRICS:
    try:
        _df = store.load_latest_metrics(_name)
        _metric_frames[_name] = _df
        print(f"{_name:<20}: {len(_df)} rows")
    except FileNotFoundError:
        _metric_frames[_name] = pd.DataFrame()
        print(f"{_name:<20}: not found")

# Unpack metrics to top-level names for easy access in debugger
term_structure: pd.DataFrame = _metric_frames["term_structure"]
skew:           pd.DataFrame = _metric_frames["skew"]
skew_ratio:     pd.DataFrame = _metric_frames["skew_ratio"]
curvature:      pd.DataFrame = _metric_frames["curvature"]
vrp:            pd.DataFrame = _metric_frames["vrp"]

# Convenience views
calls: pd.DataFrame = chain[chain["optionType"] == "CALL"].reset_index(drop=True) if not chain.empty else pd.DataFrame()
puts:  pd.DataFrame = chain[chain["optionType"] == "PUT"].reset_index(drop=True)  if not chain.empty else pd.DataFrame()

print("\n--- Available DataFrames ---")
print("  chain, calls, puts")
print("  prices")
print("  term_structure, skew, skew_ratio, curvature, vrp")
print("\nPaused. Use the Debug Console to explore any DataFrame.")

# Automatically pauses here — no manual breakpoint needed.
# Type any expression in the Debug Console: chain, skew, calls.head(), etc.
breakpoint()
