"""
Tests for src/chain_release.py.

The chain store moved out of Git LFS onto release assets on 2026-11-22,
because LFS stores a complete new copy per push and 58 nightly pushes of a
file that grew to 658MB had consumed 9.01GB of an included 10GB. It is now
one small index file plus one compressed partition per calendar month, so
no single asset approaches GitHub's 2GB limit and a nightly run transfers
the month that changed instead of the archive.

What is worth testing here is not the happy path -- a download that works is
obvious when it works -- but the refusals. The destination may be the only
copy of four months of option chains on that machine, and Schwab serves
current chains only, so anything that lands on top of it had better be
real. Every test below is a way the download could have destroyed data and
does not.

Everything runs against file:// URLs. The suite must never reach
api.github.com (see tests/conftest.py), and local files exercise the same
urllib path the real asset does.
"""

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src import chain_release
from src.schwab_database import SchwabDatabase


def _url(path: Path) -> str:
    return f"file://{path}"


@pytest.fixture(autouse=True)
def _tiny_files_are_allowed(monkeypatch):
    """The 4096-byte floor is a real guard; these fixtures are smaller.

    It exists to reject LFS pointers, HTML error pages and truncated
    transfers, which the structural check -- does it open as SQLite with the
    expected table -- catches anyway. Lowering it here keeps the fixtures
    readable without weakening what is actually being tested.
    """
    monkeypatch.setattr(chain_release, "_MIN_BYTES", 1)


def _chain(strikes: int = 3) -> pd.DataFrame:
    return pd.DataFrame({
        "symbol": [f"T 261218C{s:08d}" for s in range(strikes)],
        "optionType": ["CALL"] * strikes,
        "expiration": ["2026-12-18"] * strikes,
        "strikePrice": [float(s) for s in range(strikes)],
        "bid": [1.0] * strikes,
        "underlyingPrice": [100.0] * strikes,
        "fetchTime": ["2026-11-20T21:30:00"] * strikes,
    })


def _store(path: Path, chains: dict[date, int] | None = None, prices: int = 2) -> SchwabDatabase:
    """A real store, because what gets published is a real store."""
    db = SchwabDatabase(db_path=path)
    for day, strikes in (chains or {}).items():
        db.append_options_snapshot("SPX", day, _chain(strikes))
    if prices:
        db.append_price_history("SPX", pd.DataFrame({
            "datetime": pd.to_datetime([f"2026-11-{d:02d}" for d in range(1, prices + 1)]),
            "open": [1.0] * prices, "high": [1.0] * prices, "low": [1.0] * prices,
            "close": [1.0] * prices, "volume": [1] * prices,
        }))
    return db


class TestDownloadReplacesOnlyGoodData:
    def test_a_valid_partition_lands_and_reports_its_chains(self, tmp_path):
        source = _store(tmp_path / "src.db", {date(2026, 11, 17): 3, date(2026, 11, 18): 3})
        remote = source.partition_path(date(2026, 11, 17))
        dest = tmp_path / "out" / "2026-11.db"

        assert chain_release.download_asset("x", dest, "chains", url=_url(remote)) == 2
        assert chain_release.table_count(dest, "chains") == 2

    def test_it_creates_the_directory_it_needs(self, tmp_path):
        source = _store(tmp_path / "src.db", {date(2026, 11, 17): 3})
        remote = source.partition_path(date(2026, 11, 17))

        chain_release.download_asset(
            "x", tmp_path / "nested" / "deeper" / "2026-11.db", "chains", url=_url(remote)
        )

        assert (tmp_path / "nested" / "deeper" / "2026-11.db").exists()

    def test_an_html_error_page_does_not_replace_a_good_partition(self, tmp_path):
        """What a 404 body is: valid bytes, not a database. Opening it as
        SQLite is the check that catches it, and it has to happen before
        anything is moved into place."""
        good = _store(tmp_path / "src.db", {date(2026, 11, 17): 5})
        dest = tmp_path / "local.db"
        dest.write_bytes(good.partition_path(date(2026, 11, 17)).read_bytes())
        (tmp_path / "remote.db").write_text("<html><body>Not Found</body></html>")

        with pytest.raises(chain_release.ChainDownloadError, match="not a readable SQLite"):
            chain_release.download_asset("x", dest, "chains", url=_url(tmp_path / "remote.db"))

        assert chain_release.table_count(dest, "chains") == 1

    def test_a_missing_asset_does_not_replace_a_good_partition(self, tmp_path):
        good = _store(tmp_path / "src.db", {date(2026, 11, 17): 5})
        dest = tmp_path / "local.db"
        dest.write_bytes(good.partition_path(date(2026, 11, 17)).read_bytes())

        with pytest.raises(chain_release.ChainDownloadError):
            chain_release.download_asset(
                "x", dest, "chains", url=_url(tmp_path / "does-not-exist.db")
            )

        assert chain_release.table_count(dest, "chains") == 1

    def test_no_partial_file_is_left_behind(self, tmp_path):
        """A failed download must not leave a .download file to be mistaken
        for data, or to occupy space after a failure nobody noticed."""
        dest = tmp_path / "local.db"
        (tmp_path / "remote.db").write_text("not a database")

        with pytest.raises(chain_release.ChainDownloadError):
            chain_release.download_asset("x", dest, "chains", url=_url(tmp_path / "remote.db"))

        assert not dest.with_name(dest.name + ".download").exists()
        assert list(tmp_path.glob("*.download")) == []

    def test_a_database_without_the_expected_table_is_rejected(self, tmp_path):
        """A real SQLite file is not automatically the right SQLite file.
        Publishing the index file under a partition's name, or the reverse,
        would otherwise land silently."""
        wrong = tmp_path / "remote.db"
        connection = sqlite3.connect(wrong)
        connection.execute("CREATE TABLE something_else (x INTEGER)")
        connection.commit()
        connection.close()

        with pytest.raises(chain_release.ChainDownloadError, match="chains table"):
            chain_release.download_asset("x", tmp_path / "out.db", "chains", url=_url(wrong))


class TestAppendOnlyTablesOnlyGrow:
    def test_fewer_chains_is_refused(self, tmp_path):
        """The safety net for the whole arrangement. A run that started from
        an empty store, collected one session and published it would
        otherwise replace four months Schwab will not serve again."""
        big = _store(tmp_path / "big.db", {
            date(2026, 11, d): 3 for d in range(1, 10)
        })
        dest = tmp_path / "local.db"
        dest.write_bytes(big.partition_path(date(2026, 11, 1)).read_bytes())

        small = _store(tmp_path / "small.db", {date(2026, 11, 17): 3})

        with pytest.raises(chain_release.ChainDownloadError, match="only ever grow"):
            chain_release.download_asset(
                "x", dest, "chains", url=_url(small.partition_path(date(2026, 11, 17)))
            )

        assert chain_release.table_count(dest, "chains") == 9

    def test_more_chains_is_accepted(self, tmp_path):
        small = _store(tmp_path / "small.db", {date(2026, 11, 17): 3})
        dest = tmp_path / "local.db"
        dest.write_bytes(small.partition_path(date(2026, 11, 17)).read_bytes())
        big = _store(tmp_path / "big.db", {date(2026, 11, d): 3 for d in range(1, 5)})

        assert chain_release.download_asset(
            "x", dest, "chains", url=_url(big.partition_path(date(2026, 11, 1)))
        ) == 4

    def test_an_equal_count_is_accepted(self, tmp_path):
        """Re-collecting a date already held replaces its chain without
        changing the count, so equality has to be allowed or a corrected
        quote could never arrive."""
        source = _store(tmp_path / "src.db", {date(2026, 11, 17): 3})
        dest = tmp_path / "local.db"
        dest.write_bytes(source.partition_path(date(2026, 11, 17)).read_bytes())

        assert chain_release.download_asset(
            "x", dest, "chains", url=_url(source.partition_path(date(2026, 11, 17)))
        ) == 1

    def test_a_rollback_can_be_asked_for_explicitly(self, tmp_path):
        big = _store(tmp_path / "big.db", {date(2026, 11, d): 3 for d in range(1, 5)})
        dest = tmp_path / "local.db"
        dest.write_bytes(big.partition_path(date(2026, 11, 1)).read_bytes())
        small = _store(tmp_path / "small.db", {date(2026, 11, 17): 3})

        assert chain_release.download_asset(
            "x", dest, "chains",
            url=_url(small.partition_path(date(2026, 11, 17))),
            allow_fewer_rows=True,
        ) == 1

    def test_a_corrupt_local_file_is_not_worth_protecting(self, tmp_path):
        """Absent and corrupt mean the same thing here: there is nothing to
        lose, so the download proceeds rather than deadlocking on a file
        that cannot be read either way."""
        dest = tmp_path / "local.db"
        dest.write_text("this was never a database")
        source = _store(tmp_path / "src.db", {date(2026, 11, 17): 3})

        assert chain_release.download_asset(
            "x", dest, "chains", url=_url(source.partition_path(date(2026, 11, 17)))
        ) == 1

    def test_fewer_prices_in_the_index_file_is_refused(self, tmp_path):
        """The same rule on the other asset. The index file carries the
        prices and the record of which dates exist."""
        big = _store(tmp_path / "big.db", prices=20)
        dest = tmp_path / "local.db"
        dest.write_bytes(big.db_path.read_bytes())
        small = _store(tmp_path / "small.db", prices=2)

        with pytest.raises(chain_release.ChainDownloadError, match="only ever grow"):
            chain_release.download_asset(
                "schwab_database.db", dest, "stock_prices", url=_url(small.db_path)
            )


class TestNamingAssets:
    def test_a_month_publishes_under_its_month(self):
        assert chain_release.asset_name_for(Path("x_chains/2026-11.db")) == "chains-2026-11.db"

    def test_the_index_file_keeps_its_name(self):
        assert chain_release.asset_name_for(Path("database/schwab_database.db")) == "schwab_database.db"

    def test_months_are_read_back_out_of_a_listing(self):
        months = chain_release.published_months({
            "schwab_database.db": 1,
            "chains-2026-11.db": 2,
            "chains-2026-07.db": 3,
            "chains-2026-08.db": 4,
            "something-else.db": 5,
            "chains-not-a-month.db": 6,
        })

        assert months == ["2026-07", "2026-08", "2026-11"]

    def test_publishable_lists_the_index_first_then_every_month(self, tmp_path):
        store = _store(tmp_path / "chain.db", {
            date(2026, 10, 30): 3, date(2026, 11, 3): 3,
        })

        files = chain_release.publishable(store.db_path)

        assert [f.name for f in files] == ["chain.db", "2026-10.db", "2026-11.db"]


class TestSyncRefusesToGuess:
    def test_an_unreadable_release_is_not_an_empty_one(self, tmp_path, monkeypatch):
        """The distinction that matters. If "could not list the assets" were
        treated as "there are no assets", a GitHub outage would look like an
        archive that had been emptied, and a run would start from nothing."""
        monkeypatch.setattr(chain_release, "published_assets", lambda **kw: {})

        with pytest.raises(chain_release.ChainDownloadError, match="Refusing to guess"):
            chain_release.sync(tmp_path / "chain.db")

    def test_a_release_without_the_index_file_is_refused(self, tmp_path, monkeypatch):
        """The index carries the record of which dates exist in months this
        machine did not download. Syncing partitions without it would
        silently narrow the archive to whatever arrived."""
        monkeypatch.setattr(
            chain_release, "published_assets", lambda **kw: {"chains-2026-11.db": 10}
        )

        with pytest.raises(chain_release.ChainDownloadError, match="no record of which dates"):
            chain_release.sync(tmp_path / "chain.db")

    def test_asking_for_an_unpublished_month_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            chain_release,
            "published_assets",
            lambda **kw: {"schwab_database.db": 10, "chains-2026-11.db": 10},
        )

        with pytest.raises(chain_release.ChainDownloadError, match="Not published"):
            chain_release.sync(tmp_path / "chain.db", months=["2026-07"])


class TestSyncRefusesAPreMigrationRelease:
    """The ordering hazard, closed.

    The migration to partitions ships as its own change, and the release it
    reads from still holds the single 658MB table until a one-off workflow
    converts it. If the nightly job ran in between it would download that
    file, find no partitions, collect one session into a fresh index and
    publish an index describing one day over a release describing four
    months. Nothing in the file itself is wrong, so only this check catches
    it.
    """

    def _published(self, monkeypatch, index_path):
        monkeypatch.setattr(
            chain_release, "published_assets",
            lambda **kw: {chain_release.INDEX_ASSET: index_path.stat().st_size},
        )
        monkeypatch.setattr(chain_release, "asset_url", lambda name: _url(index_path))

    def test_an_unmigrated_index_file_is_refused(self, tmp_path, monkeypatch):
        legacy = tmp_path / "legacy.db"
        connection = sqlite3.connect(legacy)
        connection.executescript(
            "CREATE TABLE options (symbol TEXT, snapshot_date TEXT);"
            "CREATE TABLE stock_prices (symbol TEXT, price_date TEXT);"
            "INSERT INTO stock_prices VALUES ('SPX', '2026-11-03');"
        )
        connection.commit()
        connection.close()
        self._published(monkeypatch, legacy)

        with pytest.raises(chain_release.ChainDownloadError, match="pre-migration"):
            chain_release.sync(tmp_path / "local" / "chain.db")

    def test_a_migrated_index_file_is_accepted(self, tmp_path, monkeypatch):
        """No `options` table, so nothing to refuse -- even with no
        partitions published yet, which is what a brand-new store looks
        like."""
        store = _store(tmp_path / "remote.db")
        self._published(monkeypatch, store.db_path)

        result = chain_release.sync(tmp_path / "local" / "chain.db")

        assert result["downloaded"] == []

    def test_a_legacy_file_already_beside_partitions_is_accepted(self, tmp_path, monkeypatch):
        """Mid-migration on a developer's machine: the old table is still
        there for comparison and the partitions exist. That is a working
        store, not an unmigrated one."""
        legacy = tmp_path / "legacy.db"
        connection = sqlite3.connect(legacy)
        connection.executescript(
            "CREATE TABLE options (symbol TEXT);"
            "CREATE TABLE stock_prices (symbol TEXT, price_date TEXT);"
            "INSERT INTO stock_prices VALUES ('SPX', '2026-11-03');"
        )
        connection.commit()
        connection.close()
        self._published(monkeypatch, legacy)
        local = tmp_path / "local" / "chain.db"
        # prices=0 so the append-only guard on stock_prices is not what this
        # test ends up measuring -- the fixture's legacy file has one row.
        _store(local, {date(2026, 11, 3): 3}, prices=0)

        assert chain_release.sync(local)["downloaded"] == []


class TestSyncTransfersOnlyWhatChanged:
    @pytest.fixture
    def published(self, tmp_path, monkeypatch):
        """A store standing in for the release, served over file://."""
        remote = _store(tmp_path / "remote" / "chain.db", {
            date(2026, 9, 30): 3, date(2026, 10, 30): 3, date(2026, 11, 3): 3,
        })
        assets = {chain_release.INDEX_ASSET: remote.db_path.stat().st_size}
        for partition in remote.partition_paths():
            assets[chain_release.partition_asset(partition.stem)] = partition.stat().st_size

        monkeypatch.setattr(chain_release, "published_assets", lambda **kw: assets)

        def url(name: str) -> str:
            if name == chain_release.INDEX_ASSET:
                return _url(remote.db_path)
            month = name.removeprefix(chain_release.PARTITION_PREFIX).removesuffix(".db")
            return _url(remote.partition_root / f"{month}.db")

        monkeypatch.setattr(chain_release, "asset_url", url)
        return remote

    def test_a_fresh_machine_gets_everything(self, published, tmp_path):
        result = chain_release.sync(tmp_path / "local" / "chain.db")

        assert [m for m, _ in result["downloaded"]] == ["2026-09", "2026-10", "2026-11"]
        local = SchwabDatabase(db_path=tmp_path / "local" / "chain.db")
        assert local.options_snapshot_dates("SPX") == [
            date(2026, 9, 30), date(2026, 10, 30), date(2026, 11, 3)
        ]

    def test_a_second_sync_downloads_nothing(self, published, tmp_path):
        """Months that are finished never change again, which is what makes
        a sync cheap after the first one."""
        chain_release.sync(tmp_path / "local" / "chain.db")

        result = chain_release.sync(tmp_path / "local" / "chain.db")

        assert result["downloaded"] == []
        assert result["skipped"] == ["2026-09", "2026-10", "2026-11"]

    def test_newest_takes_only_the_recent_months(self, published, tmp_path):
        """What a nightly run asks for: it only appends to the current
        month, and two covers the turn of a month where a session's date and
        the calendar disagree."""
        result = chain_release.sync(tmp_path / "local" / "chain.db", newest=2)

        assert [m for m, _ in result["downloaded"]] == ["2026-10", "2026-11"]
        assert not (tmp_path / "local" / "chain_chains" / "2026-09.db").exists()

    def test_a_partial_sync_still_knows_every_date(self, published, tmp_path):
        """The reason the index is published rather than rebuilt. A run
        holding two months must still be able to report the whole archive,
        or publishing its index back would erase the months it never saw."""
        chain_release.sync(tmp_path / "local" / "chain.db", newest=1)

        local = SchwabDatabase(db_path=tmp_path / "local" / "chain.db")
        assert local.options_snapshot_dates("SPX") == [
            date(2026, 9, 30), date(2026, 10, 30), date(2026, 11, 3)
        ]
        assert local.options_snapshot("SPX", date(2026, 11, 3)).empty is False
        assert local.options_snapshot("SPX", date(2026, 9, 30)).empty, "not downloaded"

    def test_a_named_month_is_the_only_one_fetched(self, published, tmp_path):
        result = chain_release.sync(tmp_path / "local" / "chain.db", months=["2026-10"])

        assert [m for m, _ in result["downloaded"]] == ["2026-10"]


class TestCheckLocalBeforePublishing:
    def test_a_healthy_store_passes(self, tmp_path):
        store = _store(tmp_path / "chain.db", {date(2026, 11, 3): 3, date(2026, 11, 17): 3})

        result = chain_release.check_local(store.db_path)

        assert result == {
            "partitions": 1,
            "chains_in_partitions": 2,
            "chains_indexed": 2,
            "prices": 2,
            "counts": {"2026-11": 2},
            "grew": False,
        }

    def test_an_empty_store_is_refused(self, tmp_path):
        _store(tmp_path / "chain.db")

        with pytest.raises(chain_release.ChainDownloadError, match="fewer than the required"):
            chain_release.check_local(tmp_path / "chain.db")

    def test_an_index_behind_its_partitions_is_refused(self, tmp_path):
        """The shape of the accident that would narrow the archive: an index
        describing less than the data beside it, published over one that
        described more."""
        store = _store(tmp_path / "chain.db", {date(2026, 11, 3): 3, date(2026, 11, 17): 3})
        with store._connect() as conn:
            conn.execute("DELETE FROM chain_index WHERE snapshot_date = '2026-11-17'")

        with pytest.raises(chain_release.ChainDownloadError, match="index describes"):
            chain_release.check_local(store.db_path)

    def test_an_index_ahead_of_its_partitions_is_fine(self, tmp_path):
        """A nightly run holds two months and an index describing five. That
        is the normal state, not a fault."""
        store = _store(tmp_path / "chain.db", {date(2026, 11, 3): 3})
        with store._connect() as conn:
            conn.execute(
                "INSERT INTO chain_index (symbol, snapshot_date, contracts) VALUES (?, ?, ?)",
                ("SPX", "2026-07-22", 1500),
            )

        assert chain_release.check_local(store.db_path)["chains_indexed"] == 2

    def test_a_missing_store_is_refused(self, tmp_path):
        with pytest.raises(chain_release.ChainDownloadError, match="No store at"):
            chain_release.check_local(tmp_path / "nothing.db")

    def test_a_corrupt_partition_is_refused(self, tmp_path):
        store = _store(tmp_path / "chain.db", {date(2026, 11, 3): 3})
        store.partition_path(date(2026, 11, 3)).write_text("not a database any more")

        with pytest.raises(chain_release.ChainDownloadError):
            chain_release.check_local(store.db_path)


class TestNothingMayShrinkDuringARun:
    """The guard on the publish step.

    A run downloads the published state, collects into it, and uploads the
    result. Every number in that store only ever grows, so the one thing
    that must never reach the release is a smaller store -- it would replace
    four months Schwab will not serve again with the wreckage of one bad
    run. baseline() is taken right after the download; check_local()
    compares against it right before the upload.
    """

    def test_a_run_that_collected_a_session_is_allowed(self, tmp_path):
        store = _store(tmp_path / "chain.db", {date(2026, 11, 3): 3})
        before = chain_release.baseline(store.db_path)

        store.append_options_snapshot("SPX", date(2026, 11, 17), _chain(3))

        result = chain_release.check_local(store.db_path, against=before)
        assert result["grew"] is True

    def test_a_run_that_collected_nothing_is_allowed_but_flagged(self, tmp_path):
        """A run outside market hours writes nothing. That is normal, and
        the right response is to leave the release alone rather than to
        re-upload identical bytes or to fail."""
        store = _store(tmp_path / "chain.db", {date(2026, 11, 3): 3})
        before = chain_release.baseline(store.db_path)

        result = chain_release.check_local(store.db_path, against=before)

        assert result["grew"] is False

    def test_a_corrected_quote_counts_as_growth(self, tmp_path):
        """Re-collecting a date already held replaces its chain without
        changing any count, so a size change has to be the signal or a
        correction would never be published."""
        store = _store(tmp_path / "chain.db", {date(2026, 11, 3): 3})
        before = chain_release.baseline(store.db_path)

        store.append_options_snapshot("SPX", date(2026, 11, 3), _chain(9))

        # Counts are unchanged; the publish still has to happen.
        assert chain_release.check_local(store.db_path, against=before)["counts"] == \
            before["partitions"]

    def test_a_store_that_started_over_is_refused(self, tmp_path):
        """The shape of the accident, and the reason the baseline exists: a
        run that somehow began from nothing, collected one session, and
        would publish that as the whole archive.

        Trimmed through delete_options_snapshots so the result is internally
        consistent -- index and partitions agree, nothing is corrupt, every
        file opens. It is only wrong relative to where the run started,
        which is exactly the case no amount of self-inspection can catch."""
        store = _store(tmp_path / "chain.db", {date(2026, 11, d): 3 for d in range(1, 10)})
        before = chain_release.baseline(store.db_path)

        store.delete_options_snapshots(keep=[date(2026, 11, 1)])
        assert chain_release.check_local(store.db_path)["chains_indexed"] == 1, \
            "a consistent store, just a much smaller one"

        with pytest.raises(chain_release.ChainDownloadError, match="only ever grows"):
            chain_release.check_local(store.db_path, against=before)

    def test_a_partition_shrinking_on_its_own_is_refused(self, tmp_path):
        """Chains removed from a partition without the index noticing. The
        index check cannot see this one -- it still describes nine sessions
        -- so the per-partition comparison is what catches it."""
        store = _store(tmp_path / "chain.db", {date(2026, 11, d): 3 for d in range(1, 10)})
        before = chain_release.baseline(store.db_path)
        partition = store.partition_path(date(2026, 11, 1))
        connection = sqlite3.connect(partition)
        connection.execute("DELETE FROM chains WHERE snapshot_date > '2026-11-02'")
        connection.commit()
        connection.close()

        with pytest.raises(chain_release.ChainDownloadError, match="2026-11 partition held"):
            chain_release.check_local(store.db_path, against=before)

    def test_a_vanished_partition_is_refused(self, tmp_path):
        store = _store(tmp_path / "chain.db", {date(2026, 10, 30): 3, date(2026, 11, 3): 3})
        before = chain_release.baseline(store.db_path)
        store.partition_path(date(2026, 10, 30)).unlink()

        with pytest.raises(chain_release.ChainDownloadError, match="now missing"):
            chain_release.check_local(store.db_path, against=before)

    def test_lost_prices_are_refused(self, tmp_path):
        store = _store(tmp_path / "chain.db", {date(2026, 11, 3): 3}, prices=20)
        before = chain_release.baseline(store.db_path)
        with store._connect() as conn:
            conn.execute("DELETE FROM stock_prices WHERE price_date > '2026-11-02'")

        with pytest.raises(chain_release.ChainDownloadError, match="stock_prices held"):
            chain_release.check_local(store.db_path, against=before)


class TestVerify:
    def test_a_missing_file_raises(self, tmp_path):
        with pytest.raises(chain_release.ChainDownloadError, match="does not exist"):
            chain_release.verify(tmp_path / "nope.db", "chains")

    def test_the_size_floor_rejects_an_lfs_pointer(self, tmp_path, monkeypatch):
        """Restores the real floor for this one test -- the fixture lowers it
        so the other fixtures can be small."""
        monkeypatch.setattr(chain_release, "_MIN_BYTES", 4096)
        pointer = tmp_path / "chain.db"
        pointer.write_text(
            "version https://git-lfs.github.com/spec/v1\n"
            "oid sha256:9f2c1a\nsize 658022400\n"
        )

        with pytest.raises(chain_release.ChainDownloadError, match="below the"):
            chain_release.verify(pointer, "chains")

    def test_table_count_cannot_create_a_database(self, tmp_path):
        """sqlite3.connect() on a missing path creates an empty database,
        which would make "is it there?" answer yes for ever after."""
        missing = tmp_path / "not-here.db"

        with pytest.raises(sqlite3.DatabaseError):
            chain_release.table_count(missing, "chains")

        assert not missing.exists()
