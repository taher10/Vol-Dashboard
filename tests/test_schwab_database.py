"""
Tests for src/schwab_database.py's path resolution.

The committed database/schwab_database.db is tracked and the daily workflow
commits a new ~172MB version of it every night. Any local write to that same
file -- a "Refresh Live Data" click, any /api/refresh call -- leaves the
working tree dirty and blocks the next `git pull` with "local changes would
be overwritten", which happened repeatedly and cost three separate stashes.

.gitignore cannot fix that: it only applies to untracked files. SCHWAB_DB_PATH
is what actually does, by sending local writes somewhere else entirely, so
these tests pin the precedence rather than leaving it to a comment.
"""

import sqlite3

import pytest

from src.schwab_database import SchwabDatabase


class TestDatabasePathResolution:
    def test_defaults_to_the_committed_database(self, monkeypatch):
        monkeypatch.delenv("SCHWAB_DB_PATH", raising=False)
        assert SchwabDatabase().db_path.name == "schwab_database.db"

    def test_env_var_redirects_writes(self, tmp_path, monkeypatch):
        target = tmp_path / "local.db"
        monkeypatch.setenv("SCHWAB_DB_PATH", str(target))
        assert SchwabDatabase().db_path == target
        assert target.exists(), "constructing should create the file, not just name it"

    def test_explicit_argument_beats_the_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCHWAB_DB_PATH", str(tmp_path / "from-env.db"))
        explicit = tmp_path / "from-arg.db"
        assert SchwabDatabase(db_path=explicit).db_path == explicit

    def test_env_var_is_read_per_instance_not_at_import(self, tmp_path, monkeypatch):
        """Resolved in __init__ so a shell or test that sets the variable after
        import still takes effect."""
        monkeypatch.delenv("SCHWAB_DB_PATH", raising=False)
        assert SchwabDatabase().db_path.name == "schwab_database.db"
        monkeypatch.setenv("SCHWAB_DB_PATH", str(tmp_path / "later.db"))
        assert SchwabDatabase().db_path.name == "later.db"

    def test_redirected_database_is_usable_and_separate(self, tmp_path, monkeypatch):
        """The point of the redirect: a local session reads and writes its own
        file and never touches the committed one."""
        monkeypatch.setenv("SCHWAB_DB_PATH", str(tmp_path / "local.db"))
        db = SchwabDatabase()
        assert db.snapshot_symbol_counts() == []
        assert db.options_snapshot_dates("AAPL") == []

    def test_parent_directory_is_created(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCHWAB_DB_PATH", str(tmp_path / "nested" / "deeper" / "local.db"))
        assert SchwabDatabase().db_path.exists()

    def test_a_non_database_file_still_raises(self, tmp_path, monkeypatch):
        """Redirecting must not swallow a genuinely broken file -- that's the
        LFS-pointer case validate_collection handles deliberately."""
        pointer = tmp_path / "pointer.db"
        pointer.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:abc\n")
        monkeypatch.setenv("SCHWAB_DB_PATH", str(pointer))
        with pytest.raises(sqlite3.DatabaseError):
            SchwabDatabase()
