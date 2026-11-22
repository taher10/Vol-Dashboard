"""
Tests for src/chain_release.py.

These are about TRANSFER SAFETY, not about the schema. The download target
is, on any given machine, the only copy of four months of options chains
that Schwab will not serve again -- so the thing worth pinning is that a
bad transfer cannot land on top of it. A truncated body, a 404 page, a
dropped connection and a smaller-than-local asset all have to leave the
existing file exactly as it was.

That is why the module writes to a temporary file beside the destination and
only os.replace()s a file that opened as SQLite and counted its rows. The
tests below drive each failure path and assert the original survived it.

The fixture databases hold a bare `options` table rather than the real
schema from src/schwab_database.py. chain_release only ever counts rows in
that table, so reproducing the full per-contract schema would couple these
tests to a structure they do not exercise.

_MIN_BYTES is patched down throughout. It exists to reject ~134-byte LFS
pointers and few-KB error pages against a 658MB database; honouring it
literally would mean generating 100MB per test for no added coverage.
"""

import sqlite3

import pytest

from src import chain_release
from src.chain_release import ChainDownloadError


@pytest.fixture(autouse=True)
def _small_floor(monkeypatch):
    monkeypatch.setattr(chain_release, "_MIN_BYTES", 1)


def _make_db(path, rows: int):
    """A file that counts as a chain database, with `rows` contract rows."""
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE options (symbol TEXT)")
    connection.executemany("INSERT INTO options VALUES (?)", [("SPX",)] * rows)
    connection.commit()
    connection.close()
    return path


def _url(path):
    return f"file://{path}"


class TestDownloadReplacesOnlyGoodData:
    def test_a_valid_asset_lands_and_reports_its_rows(self, tmp_path):
        source = _make_db(tmp_path / "remote.db", 7)
        dest = tmp_path / "database" / "chains.db"

        assert chain_release.download(dest, _url(source)) == 7
        assert chain_release.row_count(dest) == 7

    def test_it_creates_the_destination_directory(self, tmp_path):
        """First run on a fresh checkout has no database/ directory yet."""
        source = _make_db(tmp_path / "remote.db", 3)

        chain_release.download(tmp_path / "nested" / "deeper" / "chains.db", _url(source))

        assert (tmp_path / "nested" / "deeper" / "chains.db").exists()

    def test_a_non_database_payload_leaves_the_original_untouched(self, tmp_path):
        """The 404-page case: GitHub answers with HTML, not the asset."""
        (tmp_path / "remote.db").write_bytes(b"<!DOCTYPE html><title>Not Found</title>")
        dest = _make_db(tmp_path / "chains.db", 500)

        with pytest.raises(ChainDownloadError, match="not a readable SQLite database"):
            chain_release.download(dest, _url(tmp_path / "remote.db"))

        assert chain_release.row_count(dest) == 500

    def test_an_unreachable_url_leaves_the_original_untouched(self, tmp_path):
        dest = _make_db(tmp_path / "chains.db", 500)

        with pytest.raises(ChainDownloadError):
            chain_release.download(dest, _url(tmp_path / "does-not-exist.db"))

        assert chain_release.row_count(dest) == 500

    def test_no_partial_file_survives_a_failure(self, tmp_path):
        """A leftover .download is 600MB of nothing, and the next reader
        could mistake it for the database."""
        (tmp_path / "remote.db").write_bytes(b"garbage")
        dest = tmp_path / "chains.db"

        with pytest.raises(ChainDownloadError):
            chain_release.download(dest, _url(tmp_path / "remote.db"))

        assert list(tmp_path.glob("*.download")) == []
        assert not dest.exists()


class TestTheChainTableOnlyGrows:
    """The guard that makes an upstream accident survivable.

    The chain table is append-only, so an asset with fewer rows than the
    local copy means something went wrong upstream -- a reset database, a
    half-finished upload -- not that history was revised. Replacing on that
    basis is how a session that exists nowhere else gets lost.
    """

    def test_a_smaller_asset_is_refused(self, tmp_path):
        source = _make_db(tmp_path / "remote.db", 10)
        dest = _make_db(tmp_path / "chains.db", 900)

        with pytest.raises(ChainDownloadError, match="only ever grows"):
            chain_release.download(dest, _url(source))

        assert chain_release.row_count(dest) == 900

    def test_an_equal_asset_is_allowed(self, tmp_path):
        """A day with no new session is normal, not an error."""
        source = _make_db(tmp_path / "remote.db", 900)
        dest = _make_db(tmp_path / "chains.db", 900)

        assert chain_release.download(dest, _url(source)) == 900

    def test_a_rollback_is_possible_but_has_to_be_asked_for(self, tmp_path):
        source = _make_db(tmp_path / "remote.db", 10)
        dest = _make_db(tmp_path / "chains.db", 900)

        assert chain_release.download(dest, _url(source), allow_fewer_rows=True) == 10
        assert chain_release.row_count(dest) == 10

    def test_a_corrupt_local_file_does_not_block_recovery(self, tmp_path):
        """"Unusable" and "absent" mean the same thing here -- there is
        nothing to protect, so the download must be able to repair it."""
        source = _make_db(tmp_path / "remote.db", 10)
        dest = tmp_path / "chains.db"
        dest.write_bytes(b"truncated nonsense")

        assert chain_release.download(dest, _url(source)) == 10


class TestVerify:
    def test_an_lfs_pointer_is_rejected_by_the_size_floor(self, tmp_path, monkeypatch):
        """The concrete regression: a checkout without `lfs: true` leaves a
        ~134-byte pointer, and sqlite3 reports "file is not a database" --
        which took the validator's run #2 down on 2026-09-15. The floor
        catches it first and says what it actually is."""
        monkeypatch.setattr(chain_release, "_MIN_BYTES", 100_000_000)
        pointer = tmp_path / "chains.db"
        pointer.write_text(
            "version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 690000000\n"
        )

        with pytest.raises(ChainDownloadError, match="LFS pointer"):
            chain_release.verify(pointer)

    def test_min_rows_is_enforced(self, tmp_path):
        db = _make_db(tmp_path / "chains.db", 5)

        with pytest.raises(ChainDownloadError, match="fewer than the required"):
            chain_release.verify(db, min_rows=6)

        assert chain_release.verify(db, min_rows=5) == 5

    def test_a_missing_file_says_so(self, tmp_path):
        with pytest.raises(ChainDownloadError, match="does not exist"):
            chain_release.verify(tmp_path / "absent.db")

    def test_row_count_does_not_conjure_a_database(self, tmp_path):
        """sqlite3.connect() on a missing path CREATES an empty database, so
        a plain connect would make "is the chain there?" answer yes and
        report 0 rows. The read-only URI is what prevents that."""
        missing = tmp_path / "absent.db"

        with pytest.raises(sqlite3.DatabaseError):
            chain_release.row_count(missing)

        assert not missing.exists()
