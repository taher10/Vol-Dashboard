"""
Session-wide safety net for the test suite.

Why this exists
---------------
On 2026-10-21 the committed 449MB chain database was found replaced by a
476KB file holding a single SPX day, fetched minutes earlier. The test
suite had done it: POST /api/refresh reached the real
data_loader.trigger_live_refresh(), which ran a genuine Schwab pull and
wrote the result to the default database path.

Individual tests DO guard against this -- test_demo_mode.py monkeypatches
trigger_live_refresh before calling the route. The guard stopped applying
because tests/test_api_import.py deletes every `src.*` entry from
sys.modules to force a clean reimport, so a later test patches an attribute
on a module object the application no longer uses. Nothing fails; the patch
simply lands somewhere harmless and the real function runs.

That is why no single test file reproduced it and the whole suite did.

Per-test guards cannot be trusted against that, so the protection belongs
here, applied once per session and immune to module reloading:

  * SCHWAB_DB_PATH points at a temp file, so "the default path" is never
    the committed database even if a test forgets to set it.
  * Constructing a SchwabDatabase against the real file raises instead of
    opening it, which turns a silent 449MB overwrite into a failed test
    naming the culprit.
  * Any attempt at a live Schwab pull raises. A test suite has no business
    reaching a broker: besides the data damage, Schwab rotates the refresh
    token on every use, so one stray call can invalidate the credential CI
    depends on and stop the next day's collection.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).parent.parent
_REAL_CHAIN_DB = (_PROJECT_ROOT / "database" / "schwab_database.db").resolve()
_REAL_HISTORY_DB = (_PROJECT_ROOT / "history" / "vol_history.db").resolve()


@pytest.fixture(scope="session", autouse=True)
def _never_touch_the_committed_databases(tmp_path_factory):
    """Redirect the default database path away from the committed file."""
    sandbox = tmp_path_factory.mktemp("chain-db")
    os.environ["SCHWAB_DB_PATH"] = str(sandbox / "schwab_database.db")
    yield
    os.environ.pop("SCHWAB_DB_PATH", None)


@pytest.fixture(autouse=True)
def _guard_real_files(monkeypatch, request):
    """Fail loudly if anything opens the committed databases for writing.

    Applied per test rather than per session so monkeypatch unwinds it, and
    re-resolved each time so a module reload cannot shake it off.
    """
    import src.schwab_database as schwab_database

    original_init = schwab_database.SchwabDatabase.__init__

    def guarded_init(self, db_path=None):
        resolved = schwab_database.resolve_db_path(db_path).resolve()
        if resolved == _REAL_CHAIN_DB:
            raise AssertionError(
                f"{request.node.nodeid} tried to open the committed chain database "
                f"({resolved}). Point it at tmp_path -- see tests/conftest.py."
            )
        return original_init(self, db_path)

    monkeypatch.setattr(schwab_database.SchwabDatabase, "__init__", guarded_init)


def _refuse_live_pull(*args, **kwargs):
    raise AssertionError(
        "A test attempted a live Schwab pull. That writes real data and rotates the "
        "refresh token -- rotation invalidates the SCHWAB_TOKEN_B64 secret and stops "
        "the next daily collection. Patch it in the test, or assert the route is "
        "gated instead."
    )


@pytest.fixture(autouse=True)
def _no_live_broker_calls(monkeypatch):
    """A test must never reach Schwab.

    Two things are blocked, at different depths, because one of them is not
    enough.

    trigger_live_refresh is patched on *every* copy of data_loader currently
    in sys.modules, not just the one `import` happens to return. A reimport
    can leave more than one copy loaded, and patching a single object then
    protects only whichever copy the application is not calling. That is not a
    theoretical worry: it is how a real pull reached Schwab and rotated the
    token while a guard appeared to be in place.

    OptionsVolJob.run is patched as the backstop. Patching by module attribute
    is inherently identity-sensitive, so the guard that matters most sits on
    the class that actually performs the fetch -- reached through whatever
    import path, it refuses.
    """
    import sys

    for name, module in list(sys.modules.items()):
        if name.endswith("dashboard.data_loader") and hasattr(module, "trigger_live_refresh"):
            monkeypatch.setattr(module, "trigger_live_refresh", _refuse_live_pull)

    import src.job as job

    monkeypatch.setattr(job.OptionsVolJob, "run", _refuse_live_pull)


@pytest.fixture
def reimported_src():
    """Force a genuine reimport of `src.*`, then restore sys.modules exactly.

    Several tests need the application imported from scratch -- to prove it
    boots with an unfetched LFS pointer for a database, or without schwab-py
    installed. Evicting the modules with monkeypatch.delitem looks like it
    covers that, but it leaks: delitem restores the keys it removed, while the
    reimport *adds* `src.*` entries that were not in sys.modules beforehand,
    and nothing records those. They outlive the test.

    What is left behind is a mixture. Measured on this suite, 16 modules leak,
    and sys.modules["src.api.routes"].data_loader is then a different object
    from sys.modules["src.dashboard.data_loader"]. A later
    monkeypatch.setattr(data_loader, "trigger_live_refresh", ...) patches the
    copy the application is not calling. It raises no error and guards
    nothing.

    That is not hypothetical. It is how test_demo_mode's refresh test came to
    run a real Schwab pull, which overwrote the committed 449MB chain
    database with a single SPX day. Restoring the namespace exactly --
    dropping whatever the reimport added, then putting the originals back --
    keeps module identity consistent for every test that follows.
    """
    import sys

    def src_names():
        return [m for m in sys.modules if m == "src" or m.startswith("src.")]

    saved = {name: sys.modules[name] for name in src_names()}
    for name in list(saved):
        del sys.modules[name]
    try:
        yield
    finally:
        for name in src_names():
            del sys.modules[name]
        sys.modules.update(saved)
