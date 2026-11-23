"""
Tests for src/backfill_session_dates.py.

20 of 35 stored snapshot dates sit a day ahead of the session they hold,
because collections that ran past midnight UTC were dated by the UTC clock.
Relabelling them rewrites chain partitions and the metric history, so the
properties worth pinning are the ones that make it safe to run rather than
the arithmetic.

The ordering one is the subtle part. Relabelling 10-14 -> 10-13 looks like
a total collision with the existing 10-13, until you notice 10-13 is itself
moving to 10-12 first. Processing oldest-first vacates each destination
before it is needed. The first version of this script previewed by querying
the current state instead of simulating, and claimed 375,606 rows would be
dropped when the true figure is zero -- a preview that disagrees with the
apply is worse than none, because it teaches you to ignore it.
"""

from collections import defaultdict
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src import backfill_session_dates as backfill
from src.schwab_database import SchwabDatabase, resolve_partition_root


def _db(path: Path, rows: list[tuple]) -> Path:
    """rows: (snapshot_date, symbol, contract_symbol, fetch_time)

    Written through the real store rather than hand-rolled SQL, so the
    fixtures exercise the same encoding, partitioning and index the script
    will meet in production. The chains carry only a contract symbol and a
    fetch time because those are the only columns this script reads -- the
    store fills the rest in with nulls, which is itself worth having under
    test.
    """
    grouped: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for snapshot_date, symbol, contract_symbol, fetch_time in rows:
        grouped[(symbol, snapshot_date)].append((contract_symbol, fetch_time))

    db = SchwabDatabase(db_path=path)
    for (symbol, snapshot_date), contracts in grouped.items():
        db.append_options_snapshot(
            symbol,
            date.fromisoformat(snapshot_date),
            pd.DataFrame({
                "symbol": [c for c, _ in contracts],
                "fetchTime": [f for _, f in contracts],
            }),
        )
    return path


def _dates(path: Path) -> list[str]:
    db = SchwabDatabase(db_path=path)
    with db._connect() as c:
        return [r[0] for r in c.execute(
            "SELECT DISTINCT snapshot_date FROM chain_index ORDER BY 1"
        )]


def _store_bytes(path: Path) -> dict[str, bytes]:
    """Every file the store is made of, so "untouched" can mean all of it."""
    files = {path.name: path.read_bytes()}
    root = resolve_partition_root(path)
    if root.is_dir():
        for partition in sorted(root.glob("*.db")):
            files[partition.name] = partition.read_bytes()
    return files


class TestMapping:
    def test_identifies_dates_fetched_after_midnight_utc(self, tmp_path):
        """00:30 UTC on the 15th is 20:30 ET on the 14th."""
        db = _db(tmp_path / "c.db", [("2026-10-15", "SPX", "c1", "2026-10-15T00:30:00+00:00")])

        assert backfill.build_mapping(db) == {"2026-10-15": "2026-10-14"}

    def test_leaves_correct_dates_alone(self, tmp_path):
        db = _db(tmp_path / "c.db", [("2026-10-14", "SPX", "c1", "2026-10-14T21:30:00+00:00")])

        assert backfill.build_mapping(db) == {}

    def test_uses_the_latest_fetch_in_a_snapshot(self, tmp_path):
        """A run writes its symbols over several minutes; all one session."""
        db = _db(tmp_path / "c.db", [
            ("2026-10-15", "SPX", "c1", "2026-10-15T00:30:00+00:00"),
            ("2026-10-15", "AAPL", "c2", "2026-10-15T00:36:00+00:00"),
        ])

        assert backfill.build_mapping(db) == {"2026-10-15": "2026-10-14"}


class TestCascadingRelabel:
    def test_consecutive_shifted_dates_do_not_collide(self, tmp_path):
        """The case that broke the first preview: both days shift by one, so
        each destination is occupied until the day before it has moved."""
        db = _db(tmp_path / "c.db", [
            ("2026-10-14", "SPX", "c1", "2026-10-14T00:30:00+00:00"),  # -> 10-13
            ("2026-10-15", "SPX", "c1", "2026-10-15T00:30:00+00:00"),  # -> 10-14
        ])
        mapping = backfill.build_mapping(db)

        stats = backfill._relabel(db, None, mapping)

        assert stats["dropped"] == 0, "oldest-first should make this collision-free"
        assert _dates(db) == ["2026-10-13", "2026-10-14"]

    def test_everything_is_correct_afterwards(self, tmp_path):
        db = _db(tmp_path / "c.db", [
            ("2026-10-14", "SPX", "c1", "2026-10-14T00:30:00+00:00"),
            ("2026-10-15", "SPX", "c1", "2026-10-15T00:30:00+00:00"),
        ])

        backfill._relabel(db, None, backfill.build_mapping(db))

        assert backfill.build_mapping(db) == {}, "a second pass should find nothing"

    def test_a_genuine_duplicate_is_dropped_not_crashed(self, tmp_path):
        """A partial run fetched after midnight ET belongs to a session that
        already holds that symbol. Both cannot occupy one key, so the later
        (staler) fetch loses -- now a whole chain rather than a row, because
        a chain is what the store holds."""
        db = _db(tmp_path / "c.db", [
            ("2026-07-28", "SPX", "c1", "2026-07-28T21:51:00+00:00"),  # stays
            ("2026-07-29", "SPX", "c1", "2026-07-29T05:40:00+00:00"),  # -> 07-28, clashes
        ])

        stats = backfill._relabel(db, None, backfill.build_mapping(db))

        assert stats["dropped"] == 1
        assert _dates(db) == ["2026-07-28"]
        kept = SchwabDatabase(db_path=db).options_snapshot("SPX", date(2026, 7, 28))
        assert kept["fetch_time"].iloc[0].startswith("2026-07-28"), \
            "the earlier, closer-to-close fetch should survive"


class TestDryRunIsSafe:
    def test_leaves_the_originals_untouched(self, tmp_path):
        """Every file of the store, not just the one named on the command
        line. The chains live in partitions beside it, and a dry run that
        relabelled those while leaving the index alone would be the most
        confusing possible outcome."""
        chain = _db(tmp_path / "c.db", [("2026-10-15", "SPX", "c1", "2026-10-15T00:30:00+00:00")])
        history = tmp_path / "h.db"
        before = _store_bytes(chain)

        backfill.dry_run(chain, history, tmp_path / "work")

        assert _store_bytes(chain) == before, "dry run must not modify the real store"
        assert _dates(chain) == ["2026-10-15"]

    def test_reports_what_it_would_do(self, tmp_path):
        chain = _db(tmp_path / "c.db", [("2026-10-15", "SPX", "c1", "2026-10-15T00:30:00+00:00")])

        preview = backfill.dry_run(chain, tmp_path / "h.db", tmp_path / "work")

        assert preview["mapping"] == {"2026-10-15": "2026-10-14"}
        assert preview["stats"]["moved"] == 1

    def test_nothing_to_do_returns_empty(self, tmp_path):
        chain = _db(tmp_path / "c.db", [("2026-10-14", "SPX", "c1", "2026-10-14T21:30:00+00:00")])

        assert backfill.dry_run(chain, tmp_path / "h.db", tmp_path / "work") == {}
