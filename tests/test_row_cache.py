"""
Tests for the leaderboard row cache (src/api/routes.py).

Why it exists
-------------
Precomputing the demo's metrics made loading a snapshot bundle essentially
free -- 6.7ms for all 24 symbols -- which exposed where the time actually
went. Profiling a warm scanner request:

    warm bundle fetch (24)      6.7 ms
      + z-score lookups       265.6 ms
    full _scanner_row (24)    388.6 ms   <- 98% uncached work

On the free-tier CPU the public demo runs on, roughly 30x slower than a
laptop, that was ~9.5 seconds for a leaderboard whose data had not changed.

The row helpers are pure given the two databases, so results are held until
a file changes. The two things that must hold:

  * a changed database must evict -- otherwise the live dashboard serves
    yesterday's leaderboard after a `git pull`, which is precisely the
    silent staleness this project has been bitten by before;
  * cached rows must equal uncached ones.
"""

import time

import pytest

from src.api import routes


@pytest.fixture(autouse=True)
def clear_caches():
    for fn in (routes._scanner_row, routes._term_structure_row, routes._gamma_exposure_row):
        fn.cache_clear()
    yield
    for fn in (routes._scanner_row, routes._term_structure_row, routes._gamma_exposure_row):
        fn.cache_clear()


class TestCachesAgainstTheData:
    def test_repeat_calls_hit_the_cache(self, monkeypatch):
        calls = []
        monkeypatch.setattr(routes, "_data_fingerprint", lambda: (1.0, 2.0))
        cached = routes._data_cached(lambda symbol, dte: (calls.append(symbol), {"symbol": symbol})[1])

        cached("SPX", 30)
        cached("SPX", 30)

        assert calls == ["SPX"], "second call should not have re-run the function"

    def test_changed_fingerprint_evicts(self, monkeypatch):
        """A `git pull` rewrites both databases mid-session. Without this the
        dashboard serves the previous day's rows until someone restarts."""
        calls = []
        fingerprint = [(1.0, 2.0)]
        monkeypatch.setattr(routes, "_data_fingerprint", lambda: fingerprint[0])
        cached = routes._data_cached(lambda symbol: (calls.append(symbol), {"symbol": symbol})[1])

        cached("SPX")
        fingerprint[0] = (9.0, 2.0)  # chain database changed
        cached("SPX")

        assert len(calls) == 2

    def test_history_database_also_evicts(self, monkeypatch):
        """The z-score lookups read vol_history.db, a different file from the
        chain database -- both have to be in the key."""
        calls = []
        fingerprint = [(1.0, 2.0)]
        monkeypatch.setattr(routes, "_data_fingerprint", lambda: fingerprint[0])
        cached = routes._data_cached(lambda symbol: (calls.append(symbol), {})[1])

        cached("SPX")
        fingerprint[0] = (1.0, 7.0)  # only the history database changed
        cached("SPX")

        assert len(calls) == 2

    def test_different_arguments_are_separate_entries(self, monkeypatch):
        calls = []
        monkeypatch.setattr(routes, "_data_fingerprint", lambda: (1.0, 2.0))
        cached = routes._data_cached(lambda symbol, dte: (calls.append((symbol, dte)), {})[1])

        cached("SPX", 30)
        cached("SPX", 45)
        cached("AAPL", 30)

        assert len(calls) == 3


class TestFingerprint:
    def test_tracks_both_databases(self, tmp_path, monkeypatch):
        """Points at temp files rather than the real ones. The chain
        database is absent under CI -- it is a release asset nothing in the
        tests workflow downloads, and before 2026-11-22 it was an unfetched
        Git LFS pointer. The first version of this test constructed a
        SchwabDatabase to find its path, which opens the file, and failed
        with "file is not a database". resolve_db_path exists so asking
        where the data lives never opens it."""
        chain = tmp_path / "chain.db"
        history = tmp_path / "history.db"
        chain.write_bytes(b"x")
        history.write_bytes(b"x")
        monkeypatch.setenv("SCHWAB_DB_PATH", str(chain))
        monkeypatch.setattr(routes._history_store, "db_path", history)

        chain_mark, history_mtime = routes._data_fingerprint()

        assert chain_mark == (("chain.db", chain.stat().st_mtime_ns, 1),)
        assert history_mtime == history.stat().st_mtime

    def test_a_rewritten_partition_changes_the_key(self, tmp_path, monkeypatch):
        """The whole reason the chain half is not a single mtime.

        Chains live in monthly partitions beside the index file, so
        re-collecting a date already present rewrites a partition and can
        leave the index file untouched to the nanosecond. A key that watched
        only the file SCHWAB_DB_PATH names would go on serving the chain it
        had already replaced."""
        chain = tmp_path / "chain.db"
        chain.write_bytes(b"x")
        monkeypatch.setenv("SCHWAB_DB_PATH", str(chain))
        partitions = tmp_path / "chain_chains"
        partitions.mkdir()
        partition = partitions / "2026-11.db"
        partition.write_bytes(b"first")

        index_before = chain.stat().st_mtime_ns
        before = routes._data_fingerprint()[0]
        partition.write_bytes(b"second payload")
        after = routes._data_fingerprint()[0]

        assert before != after
        # And specifically because of the partition: the index file is the
        # thing the old key watched, and it has not moved.
        assert chain.stat().st_mtime_ns == index_before

    def test_a_new_partition_changes_the_key(self, tmp_path, monkeypatch):
        """A month rolling over adds a file rather than changing one."""
        chain = tmp_path / "chain.db"
        chain.write_bytes(b"x")
        monkeypatch.setenv("SCHWAB_DB_PATH", str(chain))
        partitions = tmp_path / "chain_chains"
        partitions.mkdir()
        (partitions / "2026-11.db").write_bytes(b"november")

        before = routes._data_fingerprint()[0]
        (partitions / "2026-12.db").write_bytes(b"december")

        assert routes._data_fingerprint()[0] != before

    def test_ignores_files_that_are_not_partitions(self, tmp_path, monkeypatch):
        """A download leaves temp files beside the real ones, and a stray
        .db-journal or half-written .download must not look like data."""
        chain = tmp_path / "chain.db"
        chain.write_bytes(b"x")
        monkeypatch.setenv("SCHWAB_DB_PATH", str(chain))
        partitions = tmp_path / "chain_chains"
        partitions.mkdir()
        (partitions / "2026-11.db").write_bytes(b"november")

        before = routes._data_fingerprint()[0]
        (partitions / "2026-11.db.download").write_bytes(b"half")
        (partitions / "notes.txt").write_bytes(b"hello")

        assert routes._data_fingerprint()[0] == before

    def test_does_not_open_the_database(self, tmp_path, monkeypatch):
        """The regression, pinned: a file that is not valid SQLite -- exactly
        what an unfetched LFS pointer is -- must still fingerprint fine."""
        pointer = tmp_path / "chain.db"
        pointer.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:abc\n")
        monkeypatch.setenv("SCHWAB_DB_PATH", str(pointer))

        assert routes._data_fingerprint()[0] == (
            ("chain.db", pointer.stat().st_mtime_ns, pointer.stat().st_size),
        )

    def test_missing_file_does_not_raise(self, monkeypatch, tmp_path):
        """A fresh checkout may have neither file yet. The fingerprint has to
        degrade to a constant rather than take the API down."""
        monkeypatch.setenv("SCHWAB_DB_PATH", str(tmp_path / "absent.db"))

        assert routes._data_fingerprint()[0] == (("absent.db", 0, 0),)


class TestWrapperPreservesTheFunction:
    def test_keyword_arguments_work(self, monkeypatch):
        """_gamma_exposure_row has a default argument; a keyword call site
        must not blow up on the wrapper's signature."""
        monkeypatch.setattr(routes, "_data_fingerprint", lambda: (1.0, 2.0))
        cached = routes._data_cached(lambda symbol, target_dte=30: {"dte": target_dte})

        assert cached("SPX", target_dte=45) == {"dte": 45}
        assert cached("SPX") == {"dte": 30}

    def test_underlying_function_is_reachable(self, monkeypatch):
        """__wrapped__ lets a test or a caller bypass the cache deliberately."""
        monkeypatch.setattr(routes, "_data_fingerprint", lambda: (1.0, 2.0))
        cached = routes._data_cached(lambda symbol: {"symbol": symbol})

        assert cached.__wrapped__("SPX") == {"symbol": "SPX"}
