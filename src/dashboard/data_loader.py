"""
src/dashboard/data_loader.py

Plain-Python data-access layer used by the FastAPI backend (src/api/).
Wraps CSVStore / OptionsVolJob only — no web-framework imports here, so it
stays testable without either framework's runtime.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime, UTC
from functools import lru_cache
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
from src.schwab_database import SchwabDatabase

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


# SchwabDatabase.options_snapshot() stores snake_case; every consumer
# downstream (metrics.VolatilityMetrics, strategy_engine, vol_surface) speaks
# the camelCase shape Schwab's live API returns. This is the one place that
# translation happens for live dashboard reads -- backtest_engine delegates
# here rather than keeping a second copy, so a new Schwab column only has to
# be mapped once.
_DB_COLUMN_RENAME = {
    "option_type": "optionType",
    "strike_price": "strikePrice",
    "underlying_price": "underlyingPrice",
    "implied_volatility": "impliedVolatility",
    "open_interest": "openInterest",
    "theoretical_value": "theoreticalOptionValue",
    "fetch_time": "fetchTime",
}


def adapt_db_chain(raw: pd.DataFrame) -> pd.DataFrame:
    """One stored chain renamed into the camelCase schema the live path uses,
    so a database read is just another chain DataFrame to every consumer."""
    if raw is None or raw.empty:
        return raw
    df = raw.rename(columns=_DB_COLUMN_RENAME)
    df["expiration"] = pd.to_datetime(df["expiration"])
    return df


def _db_fingerprint(db: SchwabDatabase) -> float:
    """Mtime of the chain database, used as a cache key.

    The daily workflow commits a new database and `git pull` rewrites the
    file, so mtime changes exactly when the data does -- including when a
    pull brings in days collected while this process was running. Cheaper
    and more honest than a TTL, which would either serve stale data for its
    duration or re-read constantly.
    """
    try:
        return Path(db.db_path).stat().st_mtime
    except OSError:
        return 0.0


@lru_cache(maxsize=128)
def _cached_bundle(save_symbol: str, snapshot_date: date, _fingerprint: float) -> SnapshotBundle:
    """Build one symbol's bundle. Cached because the metrics are recomputed
    from the chain on every call (~210ms) and a cross-symbol leaderboard asks
    for all 24 -- uncached that is ~7.6s per request against ~0.3s served
    warm. `_fingerprint` is not read: it is in the signature so a changed
    database evicts every entry.

    Recomputing rather than reading stored metrics is deliberate. The metrics
    are derived entirely from the chain, so computing them from the same rows
    that are being served makes it structurally impossible for the two to
    disagree -- which is exactly the failure the CSV path allowed, where a
    precomputed metrics file could be dated differently from the chain beside
    it.
    """
    db = SchwabDatabase()
    chain = adapt_db_chain(db.options_snapshot(save_symbol, snapshot_date))

    prices = db.price_series(save_symbol)
    metrics = VolatilityMetrics(
        chain, price_history=prices if not prices.empty else None
    ).compute_all()

    if "fetchTime" in chain.columns and not chain["fetchTime"].isna().all():
        as_of = pd.Timestamp(chain["fetchTime"].max())
    else:
        as_of = pd.Timestamp(datetime.combine(snapshot_date, datetime.min.time()), tz=UTC)

    return SnapshotBundle(chain=chain, prices=prices, metrics=metrics, as_of=as_of)


def load_latest_snapshot(save_symbol: str = "SPX") -> SnapshotBundle:
    """
    Latest stored chain, price history and metrics for `save_symbol`, read
    from database/schwab_database.db.

    Reads the database rather than data/raw/*.csv because that is the store
    the daily workflow actually fills. The CSV store is written only by a
    local `python -m src.job` run and `data/raw/` is gitignored, so CI's
    output could never reach it -- on 2026-09-30 the dashboard was serving
    2026-09-23 quotes for 23 of 24 symbols while a same-day snapshot sat in
    the committed database, because the only symbol anyone had refreshed
    locally was SPX. The database arrives with every `git pull`, so the
    dashboard is now as current as the pipeline without anyone running a
    collection by hand.

    If the newest stored chain has no usable quotes (Schwab's -999 sentinel,
    see src/data_quality.py) this walks back to the most recent one that
    does, rather than rendering an all-empty dashboard.
    """
    db = SchwabDatabase()
    latest = db.latest_options_snapshot_date(save_symbol)
    if latest is None:
        raise FileNotFoundError(
            f"No stored chain snapshot for '{save_symbol}' in {db.db_path}. "
            "The daily workflow populates this; `git pull` to fetch the latest."
        )

    fingerprint = _db_fingerprint(db)
    bundle = _cached_bundle(save_symbol, latest, fingerprint)
    if is_chain_usable(bundle.chain):
        return bundle

    for candidate in reversed(db.options_snapshot_dates(save_symbol)[:-1]):
        fallback = _cached_bundle(save_symbol, candidate, fingerprint)
        if is_chain_usable(fallback.chain):
            return fallback

    # Nothing usable anywhere: serve the newest anyway. The UI's existing
    # empty-state handling covers it, and some data beats none.
    return bundle


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
    token.json) are intentionally left to propagate unmodified — the caller
    (src/api/routes.py's /api/refresh) is responsible for catching and
    reporting them.

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
