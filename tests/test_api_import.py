"""
The API must import without a usable chain database.

database/schwab_database.db is ~260MB and tracked in Git LFS. The tests
workflow checks out WITHOUT lfs: true on purpose -- fetching 260MB per run
would drain the free monthly LFS bandwidth in days -- so in CI that path
holds a pointer file, roughly 130 bytes of text:

    version https://git-lfs.github.com/spec/v1
    oid sha256:a24ff986...
    size 257855488

SQLite opens that and raises "file is not a database".

routes.py used to build its SchwabDatabase at module scope, so merely
importing the API ran DDL against that file. Every API test then errored
during collection, and the failure pointed at sqlite rather than at the
import, which is a slow thing to diagnose -- it had already cost this
project two separate incidents before this test existed.

Importing a module should not open a quarter-gigabyte file, let alone
require one to exist. These tests pin that.
"""

import importlib

import pytest

_LFS_POINTER = (
    "version https://git-lfs.github.com/spec/v1\n"
    "oid sha256:a24ff986cac966e791d0f4be0c40c3de17a7fca092c81f51bb038ce8d8601b21\n"
    "size 257855488\n"
)


@pytest.fixture
def unfetched_lfs_db(tmp_path, monkeypatch, reimported_src):
    """Point the app at a file shaped exactly like CI's unfetched pointer.

    reimported_src both clears `src.*` so the import below is real and puts
    sys.modules back exactly afterwards; see its docstring for why the
    obvious monkeypatch.delitem loop is not safe here.
    """
    pointer = tmp_path / "schwab_database.db"
    pointer.write_text(_LFS_POINTER)
    monkeypatch.setenv("SCHWAB_DB_PATH", str(pointer))
    return pointer


class TestImportsWithoutADatabase:
    def test_routes_imports(self, unfetched_lfs_db):
        importlib.import_module("src.api.routes")

    def test_app_imports_and_serves_health(self, unfetched_lfs_db):
        from fastapi.testclient import TestClient

        from src.api.main import app

        assert TestClient(app).get("/api/health").status_code == 200

    def test_symbols_served_without_touching_the_database(self, unfetched_lfs_db):
        """/api/symbols is registry data. It has no reason to need the chain
        database, and a regression that made it need one would break the
        frontend's first call on every page."""
        from fastapi.testclient import TestClient

        from src.api.main import app

        resp = TestClient(app).get("/api/symbols")

        assert resp.status_code == 200
        assert len(resp.json()) > 0

    def test_the_pointer_really_is_unusable(self, unfetched_lfs_db):
        """Guards the tests above: if sqlite could open this file they would
        pass for the wrong reason and stop protecting anything."""
        import sqlite3

        with pytest.raises(sqlite3.DatabaseError):
            sqlite3.connect(str(unfetched_lfs_db)).execute("SELECT 1 FROM options")


class TestDatabaseHandleIsReused:
    def test_same_instance_within_one_configured_path(self, tmp_path, monkeypatch):
        """Constructing SchwabDatabase opens the file and runs the schema
        script; a cross-symbol endpoint would otherwise repeat that 24 times
        per request."""
        monkeypatch.setenv("SCHWAB_DB_PATH", str(tmp_path / "a.db"))
        from src.api import routes

        routes._db_at.cache_clear()

        assert routes._db() is routes._db()

    def test_changing_the_path_yields_a_new_handle(self, tmp_path, monkeypatch):
        """A demo process pointed at a trimmed snapshot, or a test, must not
        be served the previous path's handle."""
        from src.api import routes

        routes._db_at.cache_clear()
        monkeypatch.setenv("SCHWAB_DB_PATH", str(tmp_path / "a.db"))
        first = routes._db()
        monkeypatch.setenv("SCHWAB_DB_PATH", str(tmp_path / "b.db"))

        assert routes._db() is not first
