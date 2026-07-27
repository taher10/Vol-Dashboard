"""
src/dashboard/data_loader.py

Plain-Python data-access layer for the Streamlit dashboard. Wraps CSVStore /
OptionsVolJob only — no Streamlit imports here (caching decorators belong at
the Streamlit call sites, since this module must stay testable without a
Streamlit runtime).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime, UTC
from pathlib import Path

# Ensure project root is on sys.path regardless of how this module is imported
# (mirrors src/job.py's approach).
_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd

from src.data_quality import is_chain_usable
from src.data_store import CSVStore
from src.job import OptionsVolJob
from src.metrics import VolatilityMetrics

# Metric names we attempt to load, in the order callers can expect keys to
# appear when present. "vrp" is legitimately absent when no price history was
# available at save time.
_METRIC_NAMES = ["term_structure", "skew", "skew_ratio", "curvature", "vrp"]


@dataclass
class SnapshotBundle:
    chain: pd.DataFrame
    prices: pd.DataFrame
    metrics: dict[str, pd.DataFrame]  # keys: term_structure, skew, skew_ratio, curvature, and vrp if present
    as_of: pd.Timestamp


def _fallback_to_last_usable_snapshot(store: CSVStore, prices: pd.DataFrame) -> SnapshotBundle | None:
    """
    Walk backward through every saved chain snapshot (most recent first,
    skipping the latest one that the caller already found unusable) and
    return the first one that passes is_chain_usable(), with its metrics
    recomputed fresh from that same chain+prices -- rather than trying to
    line up a separately-dated precomputed metrics/*.csv file, which could
    end up mismatched with whichever chain we fell back to.

    Returns None if no saved snapshot is usable (caller should fall back to
    serving the unusable latest one -- some data beats none).
    """
    files = store.list_snapshots()
    for path in reversed(files[:-1]):  # newest-first, excluding the already-checked latest
        chain = store.load_chain_snapshot(path)
        if not is_chain_usable(chain):
            continue
        vm = VolatilityMetrics(chain, price_history=prices if not prices.empty else None)
        metrics = vm.compute_all()
        as_of = (
            pd.Timestamp(chain["fetchTime"].max())
            if "fetchTime" in chain.columns and not chain["fetchTime"].isna().all()
            else pd.Timestamp(datetime.now(UTC))
        )
        return SnapshotBundle(chain=chain, prices=prices, metrics=metrics, as_of=as_of)
    return None


def load_latest_snapshot(save_symbol: str = "SPX") -> SnapshotBundle:
    """
    Load the latest chain, price history, and all available metrics for
    `save_symbol` via CSVStore. Only the chain is a hard requirement — a
    missing price history or any single metric is tolerated (per-metric,
    catching FileNotFoundError) so the dashboard can still render partial
    data (e.g. vrp absent because no price history existed at save time).

    If the latest saved chain has no usable quotes (Schwab's -999 sentinel —
    see src/data_quality.py — normally kept out of storage entirely by
    job.py's write-time guard, but this covers snapshots saved before that
    guard existed, or any other source of a bad file landing in data/raw/),
    falls back to the most recent snapshot that IS usable rather than
    showing an all-empty dashboard.
    """
    store = CSVStore(symbol=save_symbol)

    try:
        chain = store.load_latest_chain()
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"No chain snapshot found for '{save_symbol}'. Run the job "
            f"(python -m src.job) at least once before loading the dashboard."
        ) from exc

    try:
        prices = store.load_latest_price_history()
    except FileNotFoundError:
        prices = pd.DataFrame()

    if not is_chain_usable(chain):
        fallback = _fallback_to_last_usable_snapshot(store, prices)
        if fallback is not None:
            return fallback
        # No usable snapshot anywhere in history -- fall through and serve
        # the unusable one; the UI's existing empty-state handling (VRP/term
        # structure "no data available" messages) covers this gracefully.

    metrics: dict[str, pd.DataFrame] = {}
    for name in _METRIC_NAMES:
        try:
            metrics[name] = store.load_latest_metrics(name)
        except FileNotFoundError:
            continue

    if "fetchTime" in chain.columns and not chain["fetchTime"].isna().all():
        as_of = pd.Timestamp(chain["fetchTime"].max())
    else:
        as_of = pd.Timestamp(datetime.now(UTC))

    return SnapshotBundle(chain=chain, prices=prices, metrics=metrics, as_of=as_of)


def latest_chain_mtime(save_symbol: str = "SPX") -> float:
    """
    Return the mtime (os.path.getmtime, via Path.stat) of the most recent
    chain snapshot file for `save_symbol`, so callers can pass it as an
    explicit st.cache_data argument — CSVStore overwrites same-day files, so
    a cache keyed only on symbol would miss a same-day manual refresh.
    """
    store = CSVStore(symbol=save_symbol)
    files = store.list_snapshots()
    if not files:
        raise FileNotFoundError(
            f"No chain snapshots found for '{save_symbol}'; nothing to get an mtime from."
        )
    latest = max(files, key=lambda p: p.stat().st_mtime)
    return latest.stat().st_mtime


def list_snapshot_dates(save_symbol: str = "SPX") -> list[date]:
    """Sorted list of dates with saved chain snapshots, parsed from CSVStore filenames."""
    store = CSVStore(symbol=save_symbol)
    dates: list[date] = []
    prefix = f"{save_symbol}_chain_"
    for path in store.list_snapshots():
        stem = path.stem  # e.g. "SPX_chain_20260718"
        if not stem.startswith(prefix):
            continue
        stamp = stem[len(prefix):]
        try:
            dates.append(datetime.strptime(stamp, "%Y%m%d").date())
        except ValueError:
            continue
    return sorted(dates)


def trigger_live_refresh(
    api_symbol: str = "$SPX",
    save_symbol: str = "SPX",
    strike_increment: int | None = 100,
    strikes_each_side: int = 5,
) -> SnapshotBundle:
    """
    Run the full live pipeline (auth → fetch → save → compute metrics) via
    OptionsVolJob, overwriting today's saved CSVs, then re-read via
    load_latest_snapshot() for a consistent bundle (run() only returns
    metrics, not chain/prices). Auth/network errors (e.g. missing
    token.json) are intentionally left to propagate unmodified — the
    Streamlit UI layer is responsible for catching and displaying them.

    strike_increment/strikes_each_side are passed straight through to
    OptionsVolJob -- pass strike_increment=None and a much larger
    strikes_each_side (e.g. 20) for equities, whose tighter native strike
    spacing means SPX's validated defaults (100 / 5) don't reach 25-delta.
    See options_fetcher.fetch_monthly_chain.
    """
    job = OptionsVolJob(
        symbol=api_symbol,
        save_symbol=save_symbol,
        strike_increment=strike_increment,
        strikes_each_side=strikes_each_side,
    )
    job.run()
    return load_latest_snapshot(save_symbol)
