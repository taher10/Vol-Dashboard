"""
src/backfill_history.py

One-off/rerunnable backfill: walks every already-saved chain snapshot in
data/raw/ (from manual `python -m src.job` runs made before HistoryStore
and SchwabDatabase were wired into the fetch path, or from any snapshot
that predates this module) and writes each into both:
  - history/vol_history.db      (HistoryStore   -- derived per-expiry metrics)
  - database/schwab_database.db (SchwabDatabase -- raw options + prices)

so real on-disk history that already exists isn't wasted. Unusable
snapshots (Schwab's -999 no-quote sentinel -- see src/data_quality.py) are
skipped, same as the live-fetch path in job.py.

Safe to rerun: both stores upsert on (symbol, date, ...), so re-running
after new manual snapshots land doesn't duplicate anything.

Usage:
    python -m src.backfill_history
    python -m src.backfill_history --symbols SPX AAPL
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd

from src.data_quality import is_chain_usable
from src.data_store import CSVStore
from src.history_store import HistoryStore
from src.metrics import VolatilityMetrics
from src.schwab_database import SchwabDatabase
from src.symbols import SYMBOL_REGISTRY

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backfill_history")


def backfill_symbol(symbol: str, history: HistoryStore, schwab_db: SchwabDatabase) -> dict:
    store = CSVStore(symbol=symbol)
    files = store.list_snapshots()
    if not files:
        return {"backfilled": 0, "skipped": 0}

    try:
        fallback_prices = store.load_latest_price_history()
    except FileNotFoundError:
        fallback_prices = pd.DataFrame()

    backfilled = 0
    skipped = 0
    for path in files:
        day_stamp = store.day_stamp_from_snapshot(path)
        snapshot_date = datetime.strptime(day_stamp, "%Y%m%d").date()

        chain = store.load_chain_snapshot(path)
        if not is_chain_usable(chain):
            logger.info("%s %s: unusable chain (no live quotes that day), skipping", symbol, snapshot_date)
            skipped += 1
            continue

        try:
            prices = store.load_price_history_at(day_stamp)
        except FileNotFoundError:
            prices = fallback_prices

        vm = VolatilityMetrics(chain, price_history=prices if not prices.empty else None)
        metrics = vm.compute_all()

        history.append_snapshot(symbol, snapshot_date, metrics)
        schwab_db.append_options_snapshot(symbol, snapshot_date, chain)
        if not prices.empty:
            schwab_db.append_price_history(symbol, prices)

        backfilled += 1
        logger.info("%s %s: backfilled (%d contracts)", symbol, snapshot_date, len(chain))

    return {"backfilled": backfilled, "skipped": skipped}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Backfill HistoryStore + SchwabDatabase from chain snapshots already saved in data/raw/."
    )
    p.add_argument(
        "--symbols", nargs="+", default=list(SYMBOL_REGISTRY.keys()),
        help="Subset of symbols to backfill (default: every symbol in SYMBOL_REGISTRY)",
    )
    return p


if __name__ == "__main__":
    args = _build_parser().parse_args()
    history = HistoryStore()
    schwab_db = SchwabDatabase()

    print("\n--- Backfill summary ---")
    for symbol in args.symbols:
        result = backfill_symbol(symbol, history, schwab_db)
        print(f"  {symbol:6s}: {result['backfilled']} backfilled, {result['skipped']} skipped (unusable)")
