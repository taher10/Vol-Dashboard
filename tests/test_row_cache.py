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
        """Points at temp files rather than the committed ones. The real
        chain database is an unfetched Git LFS pointer under CI, and the
        first version of this test constructed a SchwabDatabase to find its
        path -- which opens it, and failed with "file is not a database".
        resolve_db_path exists so asking where the data lives never opens
        it."""
        chain = tmp_path / "chain.db"
        history = tmp_path / "history.db"
        chain.write_bytes(b"x")
        history.write_bytes(b"x")
        monkeypatch.setenv("SCHWAB_DB_PATH", str(chain))
        monkeypatch.setattr(routes._history_store, "db_path", history)

        chain_mtime, history_mtime = routes._data_fingerprint()

        assert chain_mtime == chain.stat().st_mtime
        assert history_mtime == history.stat().st_mtime

    def test_does_not_open_the_database(self, tmp_path, monkeypatch):
        """The regression, pinned: a file that is not valid SQLite -- exactly
        what an unfetched LFS pointer is -- must still fingerprint fine."""
        pointer = tmp_path / "chain.db"
        pointer.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:abc\n")
        monkeypatch.setenv("SCHWAB_DB_PATH", str(pointer))

        assert routes._data_fingerprint()[0] == pointer.stat().st_mtime

    def test_missing_file_does_not_raise(self, monkeypatch, tmp_path):
        """A fresh checkout may have neither file yet. The fingerprint has to
        degrade to a constant rather than take the API down."""
        monkeypatch.setenv("SCHWAB_DB_PATH", str(tmp_path / "absent.db"))

        assert routes._data_fingerprint()[0] == 0.0


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
