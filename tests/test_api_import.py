"""
The API must import without a usable chain database.

database/schwab_database.db is ~658MB and is not in the repository. Since
2026-11-22 it is a release asset that each job downloads if it needs it
(see src/chain_release.py), and most do not -- so in CI that path usually
holds NOTHING AT ALL.

Before that it was Git LFS-tracked and the tests workflow skipped the
fetch, so the path held a ~134-byte pointer instead, which SQLite opens and
rejects with "file is not a database". Both states are exercised below,
along with plain garbage: what matters is that the API does not care which
kind of unusable it is, and the absent case is now the common one rather
than a hypothetical.

routes.py used to build its SchwabDatabase at module scope, so merely
importing the API ran DDL against that file. Every API test then errored
during collection, and the failure pointed at sqlite rather than at the
import, which is a slow thing to diagnose -- it had already cost this
project two separate incidents before this test existed.

Importing a module should not open a 658MB file, let alone require one to
exist. These tests pin that.
"""

import importlib

import pytest

_LFS_POINTER = (
    "version https://git-lfs.github.com/spec/v1\n"
    "oid sha256:a24ff986cac966e791d0f4be0c40c3de17a7fca092c81f51bb038ce8d8601b21\n"
    "size 257855488\n"
)


@pytest.fixture(params=["absent", "lfs-pointer", "garbage"])
def unusable_chain_db(request, tmp_path, monkeypatch, reimported_src):
    """Point the app at each way the chain database can fail to be there.

    "absent" is what CI actually looks like now that the file is a release
    asset nothing in the tests workflow downloads. "lfs-pointer" is the
    state that produced the two incidents this file was written for, kept
    because the bytes are a real thing that was really on disk. "garbage"
    stands in for a truncated or interrupted download.

    reimported_src both clears `src.*` so the import below is real and puts
    sys.modules back exactly afterwards; see its docstring for why the
    obvious monkeypatch.delitem loop is not safe here.
    """
    path = tmp_path / "schwab_database.db"
    if request.param == "lfs-pointer":
        path.write_text(_LFS_POINTER)
    elif request.param == "garbage":
        path.write_bytes(b"\x00\x01 not a database \xff" * 64)
    monkeypatch.setenv("SCHWAB_DB_PATH", str(path))
    return path


class TestImportsWithoutADatabase:
    def test_routes_imports(self, unusable_chain_db):
        importlib.import_module("src.api.routes")

    def test_app_imports_and_serves_health(self, unusable_chain_db):
        from fastapi.testclient import TestClient

        from src.api.main import app

        assert TestClient(app).get("/api/health").status_code == 200

    def test_symbols_served_without_touching_the_database(self, unusable_chain_db):
        """/api/symbols is registry data. It has no reason to need the chain
        database, and a regression that made it need one would break the
        frontend's first call on every page."""
        from fastapi.testclient import TestClient

        from src.api.main import app

        resp = TestClient(app).get("/api/symbols")

        assert resp.status_code == 200
        assert len(resp.json()) > 0

    def test_the_database_really_is_unusable(self, unusable_chain_db):
        """Guards the tests above: if sqlite could read contracts out of
        these they would pass for the wrong reason and protect nothing.

        Note the absent case still has to be driven through a query rather
        than a connect -- sqlite3.connect() on a missing path cheerfully
        CREATES an empty database, which is why src/chain_release.py opens
        everything with mode=ro."""
        import sqlite3

        with pytest.raises(sqlite3.DatabaseError):
            sqlite3.connect(str(unusable_chain_db)).execute("SELECT 1 FROM options")


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
