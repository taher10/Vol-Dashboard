"""
Tests for the partitioned chain store in src/schwab_database.py.

The store went from one 658 MB SQLite table to compressed chains in monthly
partitions on 2026-11-22. The reason was a Git LFS allowance that would
refuse a push at 10 GB -- 9.01 GB used, 12.2 MB a session, one run of room --
and the properties worth pinning are the ones that stop that trade from
costing data rather than the compression ratio, which src/chain_codec.py's
tests already cover.

Three of them matter more than the rest:

  * A chain comes back as the exact object the old `SELECT *` returned. Every
    caller indexes those columns by name and does arithmetic on those dtypes,
    so "same numbers" is not the bar.
  * Reading never creates. sqlite3.connect() on a missing path makes an empty
    database, which would turn "we never collected August" into an August
    that exists and is empty -- indistinguishable, later, from a month that
    was collected and lost.
  * The index is derived, never the record. It can be rebuilt from the
    partitions, so losing it costs a scan. If it were the other way round,
    a corrupt 1 MB file would orphan every chain behind it.
"""

from datetime import date

import pandas as pd
import pytest

from src.schwab_database import (
    SchwabDatabase,
    resolve_partition_root,
    store_fingerprint,
)


def _chain(spot: float = 100.0, strikes: tuple[float, ...] = (95.0, 100.0, 105.0)) -> pd.DataFrame:
    """A chain shaped the way Schwab hands one over."""
    rows = []
    for strike in strikes:
        for kind in ("CALL", "PUT"):
            rows.append({
                "symbol": f"TEST  261218{kind[0]}{int(strike * 1000):08d}",
                "optionType": kind,
                "expiration": "2026-12-18",
                "dte": 30,
                "strikePrice": strike,
                "bid": round(strike / 100, 2),
                "ask": round(strike / 100 + 0.2, 2),
                "mark": round(strike / 100 + 0.1, 2),
                "last": 0.3,
                "volume": 12,
                "openInterest": 340,
                "impliedVolatility": 0.1875,
                "delta": 0.5 if kind == "CALL" else -0.5,
                "gamma": 0.0125,
                "theta": -0.0325,
                "vega": 0.0875,
                "rho": 0.0075,
                "inTheMoney": strike < spot,
                "theoreticalOptionValue": 1.125,
                "underlyingPrice": spot,
                "fetchTime": "2026-11-20T21:30:00",
            })
    return pd.DataFrame(rows)


@pytest.fixture
def store(tmp_path) -> SchwabDatabase:
    return SchwabDatabase(db_path=tmp_path / "chain.db")


class TestAChainComesBackUnchanged:
    def test_values_survive_the_round_trip(self, store):
        store.append_options_snapshot("SPX", date(2026, 11, 17), _chain(spot=6800.0))

        back = store.options_snapshot("SPX", date(2026, 11, 17))

        assert len(back) == 6
        assert set(back["symbol"]) == {"SPX"}
        assert set(back["snapshot_date"]) == {"2026-11-17"}
        assert sorted(back["strike_price"].unique()) == [95.0, 100.0, 105.0]
        assert back["underlying_price"].iloc[0] == 6800.0

    def test_the_columns_are_the_ones_callers_index(self, store):
        """adapt_db_chain() renames these by name and vol_surface reads them
        positionally-by-name afterwards. A dropped or reordered column is a
        KeyError in a page, not a test failure here, so pin the list."""
        store.append_options_snapshot("SPX", date(2026, 11, 17), _chain())

        back = store.options_snapshot("SPX", date(2026, 11, 17))

        assert list(back.columns) == [
            "symbol", "snapshot_date", "contract_symbol", "option_type", "expiration",
            "dte", "strike_price", "bid", "ask", "mark", "last", "volume",
            "open_interest", "implied_volatility", "delta", "gamma", "theta", "vega",
            "rho", "in_the_money", "theoretical_value", "underlying_price", "fetch_time",
        ]

    def test_floats_are_exact_not_merely_close(self, store):
        """The chain is the only copy of this data -- Schwab serves current
        chains only -- so the encoding has to be lossless rather than
        good enough. A JSON round-trip returns 0.30000000000000004 for a
        stored 0.3, which is why the codec is columnar instead."""
        store.append_options_snapshot("SPX", date(2026, 11, 17), _chain())

        back = store.options_snapshot("SPX", date(2026, 11, 17))

        assert back["last"].iloc[0].hex() == (0.3).hex()
        assert back["implied_volatility"].iloc[0].hex() == (0.1875).hex()

    def test_dtypes_are_what_the_old_table_produced(self, store):
        """int64 for INTEGER columns, float64 for REAL, string for TEXT --
        the affinities the pre-partition `options` table had. Callers do
        arithmetic on these; a float64 dte would quietly format as 30.0."""
        store.append_options_snapshot("SPX", date(2026, 11, 17), _chain())

        back = store.options_snapshot("SPX", date(2026, 11, 17))

        assert back["dte"].dtype == "int64"
        assert back["volume"].dtype == "int64"
        assert back["in_the_money"].dtype == "int64"
        assert back["strike_price"].dtype == "float64"
        assert str(back["expiration"].dtype) in {"str", "object"}

    def test_rows_are_ordered_by_expiration_then_strike(self, store):
        store.append_options_snapshot("SPX", date(2026, 11, 17), _chain())

        back = store.options_snapshot("SPX", date(2026, 11, 17))

        assert list(back["strike_price"]) == sorted(back["strike_price"])

    def test_re_collecting_a_date_replaces_rather_than_duplicates(self, store):
        """The daily job upserts the same snapshot_date when it re-runs, and
        a corrected quote has to win rather than sit beside the old one."""
        store.append_options_snapshot("SPX", date(2026, 11, 17), _chain(spot=6800.0))
        store.append_options_snapshot("SPX", date(2026, 11, 17), _chain(spot=6850.0))

        back = store.options_snapshot("SPX", date(2026, 11, 17))

        assert len(back) == 6
        assert back["underlying_price"].iloc[0] == 6850.0


class TestReadingNeverCreates:
    def test_a_date_never_collected_returns_the_empty_shape(self, store):
        """Not a raise and not a bare DataFrame: callers index the columns
        without checking, so a missing day has to look like an empty chain
        rather than a different kind of object."""
        back = store.options_snapshot("SPX", date(2019, 1, 2))

        assert back.empty
        assert len(back.columns) == 23
        assert "strike_price" in back.columns

    def test_reading_a_missing_month_leaves_no_file_behind(self, store):
        """sqlite3.connect() on a missing path creates an empty database.
        If a read did that, a month nobody collected would afterwards be
        indistinguishable from one that was collected and lost."""
        store.options_snapshot("SPX", date(2019, 1, 2))

        assert not (store.partition_root / "2019-01.db").exists()

    def test_a_symbol_absent_from_a_real_partition_returns_empty(self, store):
        store.append_options_snapshot("SPX", date(2026, 11, 17), _chain())

        back = store.options_snapshot("AAPL", date(2026, 11, 17))

        assert back.empty
        assert len(back.columns) == 23


class TestPartitioning:
    def test_a_chain_lands_in_its_own_month(self, store):
        store.append_options_snapshot("SPX", date(2026, 11, 17), _chain())

        assert [p.name for p in store.partition_paths()] == ["2026-11.db"]

    def test_two_months_are_two_files(self, store):
        store.append_options_snapshot("SPX", date(2026, 10, 30), _chain())
        store.append_options_snapshot("SPX", date(2026, 11, 3), _chain())

        assert [p.name for p in store.partition_paths()] == ["2026-10.db", "2026-11.db"]

    def test_partitions_sit_beside_the_file_they_belong_to(self, tmp_path):
        """Derived from db_path rather than configured separately, so a test
        or a demo build that redirects the store cannot redirect half of
        it."""
        store = SchwabDatabase(db_path=tmp_path / "scratch.db")
        store.append_options_snapshot("SPX", date(2026, 11, 17), _chain())

        assert store.partition_root == tmp_path / "scratch_chains"
        assert (tmp_path / "scratch_chains" / "2026-11.db").exists()

    def test_a_month_of_chains_is_far_smaller_than_the_table_it_replaces(self, store):
        """Not a precise ratio -- chain_codec's tests own that -- just the
        property that made the change worth making. The old table averaged
        ~322 bytes per contract row once its three indexes are counted;
        anything near that would mean the encoding had stopped earning its
        place."""
        for day in range(2, 21):
            store.append_options_snapshot(
                "SPX", date(2026, 11, day), _chain(strikes=tuple(float(s) for s in range(5000, 5400)))
            )

        with store._connect() as conn:
            rows = conn.execute("SELECT SUM(contracts) FROM chain_index").fetchone()[0]
        assert rows == 19 * 800
        partition = store.partition_path(date(2026, 11, 17))
        assert partition.stat().st_size / rows < 32


class TestTheIndexIsDerivedNeverTheRecord:
    def test_dates_and_counts_come_from_the_index(self, store):
        store.append_options_snapshot("SPX", date(2026, 10, 30), _chain())
        store.append_options_snapshot("SPX", date(2026, 11, 3), _chain())
        store.append_options_snapshot("AAPL", date(2026, 11, 3), _chain())

        assert store.options_snapshot_dates("SPX") == [date(2026, 10, 30), date(2026, 11, 3)]
        assert store.latest_options_snapshot_date("SPX") == date(2026, 11, 3)
        assert store.snapshot_symbol_counts(limit=5) == [
            (date(2026, 11, 3), 2),
            (date(2026, 10, 30), 1),
        ]

    def test_rebuild_reconstructs_it_from_the_partitions_alone(self, store):
        store.append_options_snapshot("SPX", date(2026, 10, 30), _chain())
        store.append_options_snapshot("AAPL", date(2026, 11, 3), _chain())

        with store._connect() as conn:
            conn.execute("DELETE FROM chain_index")
        assert store.options_snapshot_dates("SPX") == []

        assert store.rebuild_index() == 2
        assert store.options_snapshot_dates("SPX") == [date(2026, 10, 30)]
        assert store.options_snapshot_dates("AAPL") == [date(2026, 11, 3)]

    def test_rebuild_forgets_a_partition_that_was_deleted(self, store):
        """Replaced wholesale rather than merged: a month that is gone has to
        leave the index, or the dashboard offers a date it cannot serve."""
        store.append_options_snapshot("SPX", date(2026, 10, 30), _chain())
        store.append_options_snapshot("SPX", date(2026, 11, 3), _chain())

        store.partition_path(date(2026, 10, 30)).unlink()
        store.rebuild_index()

        assert store.options_snapshot_dates("SPX") == [date(2026, 11, 3)]

    def test_an_empty_index_beside_real_partitions_heals_itself(self, tmp_path):
        """What a fresh sync looks like: partitions arrive as files, with no
        index describing them. Rebuilding is pure derivation and always
        cheaper than a dashboard reporting no history for data it is sitting
        on."""
        original = SchwabDatabase(db_path=tmp_path / "chain.db")
        original.append_options_snapshot("SPX", date(2026, 11, 17), _chain())

        fresh_dir = tmp_path / "downloaded"
        fresh_dir.mkdir()
        partitions = resolve_partition_root(fresh_dir / "chain.db")
        partitions.mkdir()
        for source in original.partition_paths():
            (partitions / source.name).write_bytes(source.read_bytes())

        arrived = SchwabDatabase(db_path=fresh_dir / "chain.db")

        assert arrived.options_snapshot_dates("SPX") == [date(2026, 11, 17)]
        assert len(arrived.options_snapshot("SPX", date(2026, 11, 17))) == 6

    def test_healing_does_not_overwrite_an_index_that_has_rows(self, store):
        """Only the empty case heals. Rebuilding on every construction would
        be a scan per request, and the index is written by the same call that
        writes the chain -- so a populated index is current by
        construction."""
        store.append_options_snapshot("SPX", date(2026, 11, 17), _chain())
        with store._connect() as conn:
            conn.execute(
                "INSERT INTO chain_index (symbol, snapshot_date, contracts) VALUES (?, ?, ?)",
                ("MANUAL", "2026-11-18", 7),
            )

        reopened = SchwabDatabase(db_path=store.db_path)

        assert reopened.options_snapshot_dates("MANUAL") == [date(2026, 11, 18)]


class TestContractHistoryStillCrossesDates:
    def test_it_spans_partitions(self, store):
        """The one read the layout does not favour: it crosses every date
        instead of selecting one. It cost an 80 MB index to serve before,
        for an endpoint the frontend has never called."""
        for day in (date(2026, 10, 30), date(2026, 11, 3), date(2026, 11, 17)):
            store.append_options_snapshot("SPX", day, _chain(spot=100.0 + day.day))
        contract = _chain()["symbol"].iloc[0]

        history = store.contract_history("SPX", contract)

        assert list(history["snapshot_date"]) == ["2026-10-30", "2026-11-03", "2026-11-17"]
        assert len(history) == 3

    def test_an_unknown_contract_returns_the_empty_shape(self, store):
        store.append_options_snapshot("SPX", date(2026, 11, 17), _chain())

        history = store.contract_history("SPX", "NOPE 261218C00100000")

        assert history.empty
        assert len(history.columns) == 23


class TestTrimmingForADemo:
    def test_a_partition_keeping_nothing_is_deleted_outright(self, store):
        """An emptied SQLite file still occupies its pages, and the point of
        the operation is size."""
        store.append_options_snapshot("SPX", date(2026, 10, 30), _chain())
        store.append_options_snapshot("SPX", date(2026, 11, 17), _chain())

        removed = store.delete_options_snapshots(keep=[date(2026, 11, 17)])

        assert removed == 1
        assert [p.name for p in store.partition_paths()] == ["2026-11.db"]
        assert store.options_snapshot_dates("SPX") == [date(2026, 11, 17)]

    def test_a_partition_keeping_something_is_thinned_and_shrinks(self, store):
        """VACUUM is what actually reclaims the pages. Without it the file
        stays its original size and the trim achieves nothing -- which is
        what the demo build is for.

        Enough dates to clear SQLite's ~12 KB floor for a file: below that
        a shrink is unobservable no matter how much is deleted, and a test
        that cannot see the thing it checks is worse than none.
        """
        wide = tuple(float(strike) for strike in range(5000, 5400))
        for day in range(2, 21):
            store.append_options_snapshot("SPX", date(2026, 11, day), _chain(strikes=wide))
        partition = store.partition_path(date(2026, 11, 17))
        before = partition.stat().st_size
        assert before > 20_000, "fixture too small for a shrink to be visible"

        store.delete_options_snapshots(keep=[date(2026, 11, 17)])

        assert partition.stat().st_size < before
        assert len(store.options_snapshot("SPX", date(2026, 11, 17))) == 800
        assert store.options_snapshot("SPX", date(2026, 11, 3)).empty

    def test_keeping_nothing_empties_the_store(self, store):
        store.append_options_snapshot("SPX", date(2026, 11, 17), _chain())

        store.delete_options_snapshots(keep=[])

        assert store.partition_paths() == []
        assert store.options_snapshot_dates("SPX") == []


class TestRelabellingASnapshotDate:
    def test_it_moves_a_chain_within_a_partition(self, store):
        store.append_options_snapshot("SPX", date(2026, 11, 17), _chain(spot=6800.0))

        assert store.relabel_snapshot_date(date(2026, 11, 17), date(2026, 11, 16)) == 1

        assert store.options_snapshot_dates("SPX") == [date(2026, 11, 16)]
        assert store.options_snapshot("SPX", date(2026, 11, 16))["underlying_price"].iloc[0] == 6800.0

    def test_it_moves_a_chain_across_a_month_boundary(self, store):
        """The reason this is a store operation rather than an UPDATE: a day
        that shifts back from the 1st changes which file holds it."""
        store.append_options_snapshot("SPX", date(2026, 11, 1), _chain())

        assert store.relabel_snapshot_date(date(2026, 11, 1), date(2026, 10, 31)) == 1

        assert [p.name for p in store.partition_paths()] == ["2026-10.db"]
        assert store.options_snapshot_dates("SPX") == [date(2026, 10, 31)]

    def test_the_destination_wins_a_collision(self, store):
        """A partial run fetched after midnight ET belongs to a session that
        may already be fully collected. The later fetch is the staler one --
        hours past the close, thin and wide -- so what is already there
        survives."""
        store.append_options_snapshot("SPX", date(2026, 7, 28), _chain(spot=111.0))
        store.append_options_snapshot("SPX", date(2026, 7, 29), _chain(spot=999.0))

        moved = store.relabel_snapshot_date(date(2026, 7, 29), date(2026, 7, 28))

        assert moved == 0
        assert store.options_snapshot_dates("SPX") == [date(2026, 7, 28)]
        assert store.options_snapshot("SPX", date(2026, 7, 28))["underlying_price"].iloc[0] == 111.0

    def test_a_symbol_the_destination_lacks_still_moves(self, store):
        store.append_options_snapshot("SPX", date(2026, 7, 28), _chain())
        store.append_options_snapshot("SPX", date(2026, 7, 29), _chain())
        store.append_options_snapshot("AAPL", date(2026, 7, 29), _chain())

        moved = store.relabel_snapshot_date(date(2026, 7, 29), date(2026, 7, 28))

        assert moved == 1
        assert store.options_snapshot_dates("AAPL") == [date(2026, 7, 28)]

    def test_relabelling_a_date_with_no_chains_is_a_no_op(self, store):
        store.append_options_snapshot("SPX", date(2026, 11, 17), _chain())

        assert store.relabel_snapshot_date(date(2026, 3, 3), date(2026, 3, 2)) == 0
        assert store.options_snapshot_dates("SPX") == [date(2026, 11, 17)]


class TestPricesStayInTheIndexFile:
    def test_prices_round_trip(self, store):
        prices = pd.DataFrame({
            "datetime": pd.to_datetime(["2026-11-16", "2026-11-17"]),
            "open": [100.0, 101.0],
            "high": [102.0, 103.0],
            "low": [99.0, 100.5],
            "close": [101.5, 102.5],
            "volume": [1_000_000, 1_200_000],
        })

        assert store.append_price_history("SPX", prices) == 2

        series = store.price_series("SPX")
        assert list(series["close"]) == [101.5, 102.5]

    def test_prices_do_not_need_a_partition(self, store):
        """A symbol can have prices and no chains -- the price fetch and the
        chain fetch fail independently -- so prices must not depend on a
        partition existing."""
        store.append_price_history("SPX", pd.DataFrame({
            "datetime": pd.to_datetime(["2026-11-17"]),
            "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1],
        }))

        assert store.partition_paths() == []
        assert len(store.price_series("SPX")) == 1


class TestTheFingerprintCoversTheWholeStore:
    def test_writing_a_chain_changes_it(self, store, monkeypatch):
        monkeypatch.setenv("SCHWAB_DB_PATH", str(store.db_path))
        before = store_fingerprint()

        store.append_options_snapshot("SPX", date(2026, 11, 17), _chain())

        assert store_fingerprint() != before

    def test_rewriting_the_same_date_changes_it(self, store, monkeypatch):
        """The regression this exists for. The index row is byte-identical
        when the contract count is unchanged, so SQLite leaves the index
        file alone to the nanosecond -- and a key watching only that file
        kept serving the chain it had already replaced."""
        monkeypatch.setenv("SCHWAB_DB_PATH", str(store.db_path))
        store.append_options_snapshot("SPX", date(2026, 11, 17), _chain(spot=6800.0))
        before = store_fingerprint()

        store.append_options_snapshot("SPX", date(2026, 11, 17), _chain(spot=6850.0))

        assert store_fingerprint() != before
        assert store.options_snapshot("SPX", date(2026, 11, 17))["underlying_price"].iloc[0] == 6850.0
