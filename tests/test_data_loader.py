"""
Tests for src/dashboard/data_loader.py's snapshot reads.

These exist because of a defect found on 2026-09-30: the dashboard read
data/raw/*.csv via CSVStore, but `data/raw/` is gitignored and only a local
`python -m src.job` run ever writes it. The daily workflow fills
database/schwab_database.db instead, which IS committed. So the pipeline ran
green for a week while every page served 2026-09-23 quotes for 23 of the 24
symbols -- the sole exception being SPX, because someone had happened to run
it locally. Nothing looked broken: the numbers were arithmetically correct,
just priced off week-old chains.

The tests below pin the properties that make that specific failure
impossible to reintroduce: reads come from the database, they follow the
newest stored date, and a changed database is never served from cache.
"""

from datetime import UTC, date, datetime

import pandas as pd
import pytest

from src.dashboard import data_loader
from src.dashboard.data_loader import adapt_db_chain, load_latest_snapshot
from src.schwab_database import SchwabDatabase


# Fixed strikes, so the contract symbols are stable across calls and a second
# write for the same date genuinely upserts rather than inserting a parallel
# set of contracts -- which is what re-running the daily job does.
_STRIKES = ((95.0, 0.70), (100.0, 0.50), (105.0, 0.30))


def _chain(underlying: float, iv: float) -> pd.DataFrame:
    """A small but *usable* chain in the live camelCase schema
    append_options_snapshot() consumes (`symbol` is the contract symbol
    there, not the underlying). is_chain_usable() rejects -999 sentinels, so
    these have to look like real quotes."""
    rows = []
    for expiration, dte in ((pd.Timestamp("2026-10-16"), 16), (pd.Timestamp("2026-11-20"), 51)):
        for strike, delta in _STRIKES:
            for option_type in ("CALL", "PUT"):
                rows.append({
                    "symbol": f"X{strike:g}{option_type}{dte}",
                    "optionType": option_type,
                    "expiration": expiration,
                    "dte": dte,
                    "strikePrice": strike,
                    "bid": 1.0, "ask": 1.2, "mark": 1.1, "last": 1.1,
                    "volume": 10, "openInterest": 100,
                    "impliedVolatility": iv,
                    "delta": delta if option_type == "CALL" else -delta,
                    "gamma": 0.01, "theta": -0.05, "vega": 0.10, "rho": 0.01,
                    "inTheMoney": False,
                    "theoreticalOptionValue": 1.1,
                    "underlyingPrice": float(underlying),
                    "fetchTime": pd.Timestamp("2026-09-30 20:00:00", tz="UTC"),
                })
    return pd.DataFrame(rows)


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A hermetic database. Never touches the committed 217MB one, which under
    CI is an unfetched LFS pointer -- see test_schwab_database.py."""
    monkeypatch.setenv("SCHWAB_DB_PATH", str(tmp_path / "chains.db"))
    data_loader._cached_bundle.cache_clear()
    yield SchwabDatabase()
    data_loader._cached_bundle.cache_clear()


class TestAdaptDbChain:
    def test_renames_stored_columns_to_the_live_schema(self):
        stored = pd.DataFrame([{
            "option_type": "CALL", "strike_price": 100.0, "underlying_price": 101.0,
            "implied_volatility": 30.0, "expiration": "2026-10-16",
        }])
        out = adapt_db_chain(stored)

        for camel in ("optionType", "strikePrice", "underlyingPrice", "impliedVolatility"):
            assert camel in out.columns
        for snake in ("option_type", "strike_price", "underlying_price", "implied_volatility"):
            assert snake not in out.columns

    def test_empty_input_passes_through(self):
        empty = pd.DataFrame()
        assert adapt_db_chain(empty).empty


class TestReadsFromTheDatabase:
    def test_serves_the_newest_stored_snapshot(self, db):
        db.append_options_snapshot("AAPL", date(2026, 9, 23), _chain(100.0, 30.0))
        db.append_options_snapshot("AAPL", date(2026, 9, 30), _chain(110.0, 40.0))

        bundle = load_latest_snapshot("AAPL")

        # The 09-30 chain, not the 09-23 one that a stale CSV store held.
        assert bundle.chain["underlyingPrice"].iloc[0] == 110.0
        assert bundle.chain["impliedVolatility"].iloc[0] == 40.0

    def test_missing_symbol_raises_filenotfound(self, db):
        """routes._load_bundle() turns this into a 404, so the type matters."""
        with pytest.raises(FileNotFoundError):
            load_latest_snapshot("NOPE")

    def test_metrics_are_computed_from_the_served_chain(self, db):
        """Recomputing rather than reading a stored metrics file is what makes
        chain and metrics structurally impossible to date-mismatch."""
        db.append_options_snapshot("AAPL", date(2026, 9, 30), _chain(110.0, 40.0))

        bundle = load_latest_snapshot("AAPL")

        assert "term_structure" in bundle.metrics
        assert not bundle.metrics["term_structure"].empty


class TestCacheInvalidation:
    def test_same_day_data_changing_underneath_is_not_served_from_cache(self, db):
        """The case a symbol+date cache key would miss. A `git pull` can
        rewrite the database while this process runs, and the daily job
        upserts the *same* snapshot_date when it re-runs -- so the date is
        unchanged while the rows are not. Keying on the file's mtime is what
        catches that; without it the dashboard serves the superseded chain
        until someone restarts the server, which is the same silent staleness
        this whole change exists to remove."""
        db.append_options_snapshot("AAPL", date(2026, 9, 30), _chain(110.0, 40.0))
        assert load_latest_snapshot("AAPL").chain["underlyingPrice"].iloc[0] == 110.0

        # Same date, corrected quotes.
        db.append_options_snapshot("AAPL", date(2026, 9, 30), _chain(118.0, 44.0))

        assert load_latest_snapshot("AAPL").chain["underlyingPrice"].iloc[0] == 118.0

    def test_a_newer_date_is_picked_up(self, db):
        db.append_options_snapshot("AAPL", date(2026, 9, 30), _chain(110.0, 40.0))
        assert load_latest_snapshot("AAPL").chain["underlyingPrice"].iloc[0] == 110.0

        db.append_options_snapshot("AAPL", date(2026, 10, 1), _chain(125.0, 45.0))

        assert load_latest_snapshot("AAPL").chain["underlyingPrice"].iloc[0] == 125.0

    def test_repeat_reads_are_cached(self, db):
        """The leaderboards call this once per symbol per request; uncached
        that was ~7.6s against ~0.3s warm."""
        db.append_options_snapshot("AAPL", date(2026, 9, 30), _chain(110.0, 40.0))

        load_latest_snapshot("AAPL")
        hits_before = data_loader._cached_bundle.cache_info().hits
        load_latest_snapshot("AAPL")

        assert data_loader._cached_bundle.cache_info().hits == hits_before + 1
