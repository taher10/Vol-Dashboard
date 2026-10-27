"""
Tests for src/api/demo.py and the routes it removes.

The point of these is that the gate fails CLOSED. Nothing in this API is
authenticated, so on a public deployment the only thing standing between a
stranger and a credential-backed route is this flag:

  /api/refresh  rotates the Schwab refresh token as a side effect of running,
                which desynchronises SCHWAB_TOKEN_B64 and stops the daily
                collection until someone re-authorises in a browser.
  /api/chat     spends ANTHROPIC_API_KEY with no per-caller cap.

A regression here is not a broken feature, it is an open door, and it would
not be visible from the UI -- the buttons are hidden in demo mode, so the
routes would sit exposed with nothing on screen to suggest it. Hence tests
rather than trust.
"""

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    """A TestClient factory that can flip DEMO_MODE per test.

    demo_mode() reads the environment per call rather than at import, so the
    app does not need rebuilding between cases -- that property is itself
    worth having, and test_flag_is_read_per_call_not_at_import pins it.
    """
    from src.api.main import app

    return TestClient(app, raise_server_exceptions=False)


class TestDemoFlagParsing:
    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " 1 "])
    def test_truthy_values_enable_it(self, monkeypatch, value):
        from src.api.demo import demo_mode

        monkeypatch.setenv("DEMO_MODE", value)
        assert demo_mode() is True

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
    def test_everything_else_leaves_it_off(self, monkeypatch, value):
        from src.api.demo import demo_mode

        monkeypatch.setenv("DEMO_MODE", value)
        assert demo_mode() is False

    def test_unset_defaults_to_off(self, monkeypatch):
        """A developer who sets nothing gets the full local dashboard."""
        from src.api.demo import demo_mode

        monkeypatch.delenv("DEMO_MODE", raising=False)
        assert demo_mode() is False

    def test_flag_is_read_per_call_not_at_import(self, monkeypatch):
        from src.api import demo

        monkeypatch.delenv("DEMO_MODE", raising=False)
        assert demo.demo_mode() is False
        monkeypatch.setenv("DEMO_MODE", "1")
        assert demo.demo_mode() is True

        importlib.reload(demo)  # survives a reload too
        assert demo.demo_mode() is True


class TestCredentialBackedRoutesAreRemoved:
    def test_refresh_is_gone_in_demo_mode(self, client, monkeypatch):
        monkeypatch.setenv("DEMO_MODE", "1")

        assert client.post("/api/refresh?symbols=SPX").status_code == 404

    def test_chat_is_gone_in_demo_mode(self, client, monkeypatch):
        monkeypatch.setenv("DEMO_MODE", "1")

        assert client.post("/api/chat", json={"message": "hi", "history": []}).status_code == 404

    def test_refresh_does_not_reach_schwab_in_demo_mode(self, client, monkeypatch):
        """The status code alone would still pass if the gate ran *after* the
        live pull. Blow up if anything tries to authenticate."""
        from src.dashboard import data_loader

        def _boom(*args, **kwargs):
            raise AssertionError("demo mode must not trigger a live Schwab pull")

        monkeypatch.setattr(data_loader, "trigger_live_refresh", _boom)
        monkeypatch.setenv("DEMO_MODE", "1")

        assert client.post("/api/refresh?symbols=SPX").status_code == 404

    def test_404_not_403(self, client, monkeypatch):
        """403 confirms the route exists and is switched off, which invites
        probing. 404 is indistinguishable from never having been written."""
        monkeypatch.setenv("DEMO_MODE", "1")

        assert client.post("/api/refresh?symbols=SPX").status_code != 403


class TestReadOnlyRoutesStillWork:
    def test_health_reports_the_flag(self, client, monkeypatch):
        """The frontend hides its demo-gated controls off this value, so it
        has to be truthful in both directions."""
        monkeypatch.setenv("DEMO_MODE", "1")
        assert client.get("/api/health").json()["demo_mode"] is True

        monkeypatch.setenv("DEMO_MODE", "0")
        assert client.get("/api/health").json()["demo_mode"] is False

    def test_symbols_still_served_in_demo_mode(self, client, monkeypatch):
        """The demo is meant to look like the real dashboard -- only the two
        credentialed routes go."""
        monkeypatch.setenv("DEMO_MODE", "1")

        resp = client.get("/api/symbols")

        assert resp.status_code == 200
        assert len(resp.json()) > 0


class TestLocalBehaviourIsUnchanged:
    def test_refresh_route_is_reachable_when_flag_is_off(self, client, monkeypatch):
        """Not asserting a live pull -- just that the gate is not what stops
        it. Anything other than 404 means the route still exists locally."""
        from src.dashboard import data_loader

        monkeypatch.delenv("DEMO_MODE", raising=False)
        monkeypatch.setattr(
            data_loader, "trigger_live_refresh",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network in tests")),
        )

        assert client.post("/api/refresh?symbols=SPX").status_code != 404


class TestDemoNeedsNoCredentialedLibraries:
    """The demo deployment installs requirements-demo.txt, which omits
    schwab-py and anthropic.

    That is deliberate defence in depth. DEMO_MODE removes the two routes,
    but a flag is one edit away from being wrong; a box where the SDKs are
    not installed cannot reach Schwab or Anthropic even if the gate failed.
    It only holds while nothing imports them at module scope, which is easy
    to undo by accident -- `from src.job import OptionsVolJob` at the top of
    a dashboard module would quietly restore the dependency and nobody would
    notice until a deploy broke.
    """

    def test_api_boots_without_schwab_or_anthropic(self, monkeypatch, reimported_src):
        import builtins
        import sys

        blocked = {"schwab", "anthropic", "authlib"}
        real_import = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name.split(".")[0] in blocked:
                raise ModuleNotFoundError(f"No module named {name!r}")
            return real_import(name, *args, **kwargs)

        for mod in [m for m in sys.modules if m.split(".")[0] in blocked]:
            monkeypatch.delitem(sys.modules, mod, raising=False)
        # reimported_src has already cleared `src.*`, so the import below is
        # genuine, and it restores sys.modules exactly on the way out.
        monkeypatch.setattr(builtins, "__import__", guarded)
        monkeypatch.setenv("DEMO_MODE", "1")

        from fastapi.testclient import TestClient

        from src.api.main import app

        client = TestClient(app, raise_server_exceptions=False)

        assert client.get("/api/health").json()["demo_mode"] is True
        assert client.get("/api/symbols").status_code == 200
        assert client.post("/api/refresh?symbols=SPX").status_code == 404
