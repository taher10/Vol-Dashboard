"""
Tests for src/reauth.py.

The behaviour worth pinning here is the decision reauth() makes *before*
doing anything: whether a browser login is actually required. On 2026-09-23
the browser flow was completed and token.json was valid for a week, but the
upload to SCHWAB_TOKEN_B64 never happened -- so every workflow kept failing
on a long-dead token while a perfectly good one sat on disk. Asking someone
to log in again to fix an upload problem is how that week was lost, so the
"still valid -> just upload it" path is the one that has to stay correct.

The browser flow itself isn't exercised: it requires a real Schwab login and
rotates a live credential.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest

from src import reauth as reauth_module
from src.reauth import TOKEN_LIFETIME_DAYS, _is_loopback, _token_created_at, reauth


def _write_token(path, created_at: datetime) -> None:
    path.write_text(json.dumps({
        "creation_timestamp": int(created_at.timestamp()),
        "token": {"refresh_token": "x", "access_token": "y", "expires_at": 0},
    }))


class TestLoopbackDetection:
    """Picks the zero-paste local-server flow only when the callback actually
    comes back to this machine."""

    @pytest.mark.parametrize("url", [
        "https://127.0.0.1:8182",
        "https://localhost:8182/callback",
        "http://127.0.0.1",
    ])
    def test_loopback_callbacks(self, url):
        assert _is_loopback(url) is True

    @pytest.mark.parametrize("url", [
        "https://oauth.pstmn.io/v1/callback",  # the default many people start with
        "https://example.com/cb",
    ])
    def test_remote_callbacks(self, url):
        assert _is_loopback(url) is False

    def test_hostname_not_substring(self):
        """A host merely *containing* "localhost" is not loopback -- matching
        on substring would send the flow to a local server that never
        receives the redirect."""
        assert _is_loopback("https://localhost.evil.com/cb") is False


class TestTokenCreatedAt:
    def test_reads_creation_timestamp(self, tmp_path):
        created = datetime(2026, 9, 23, 5, 48, tzinfo=UTC)
        path = tmp_path / "token.json"
        _write_token(path, created)
        assert _token_created_at(path) == created

    def test_missing_file_is_none(self, tmp_path):
        assert _token_created_at(tmp_path / "nope.json") is None

    def test_unparseable_file_is_none(self, tmp_path):
        """A half-written token must not crash the tool -- it should fall
        through to the browser flow, which is the recovery path."""
        path = tmp_path / "token.json"
        path.write_text("{not json")
        assert _token_created_at(path) is None


class _FakeAuth:
    callback_url = "https://oauth.pstmn.io/v1/callback"
    api_key = "k"
    app_secret = "s"
    timeout = 30.0

    def __init__(self, token_path):
        self.token_path = token_path


@pytest.fixture
def no_browser(monkeypatch):
    """Fail loudly if a test ever reaches the browser flow."""
    def _boom(*args, **kwargs):
        raise AssertionError("browser login flow should not have been reached")

    monkeypatch.setattr(reauth_module.schwab.auth, "client_from_manual_flow", _boom)
    monkeypatch.setattr(reauth_module.schwab.auth, "client_from_login_flow", _boom)


class TestSkipsLoginWhenTokenStillValid:
    """The 2026-09-23 failure, encoded: a valid token on disk plus a stale
    secret needs an upload, not a login."""

    @pytest.fixture(autouse=True)
    def _wire(self, tmp_path, monkeypatch, no_browser):
        self.token_path = tmp_path / "token.json"
        monkeypatch.setattr(
            reauth_module.SchwabAuth, "from_env",
            classmethod(lambda cls: _FakeAuth(self.token_path)),
        )
        self.pushed = []
        monkeypatch.setattr(
            reauth_module, "_push_to_secret",
            lambda path, created: (self.pushed.append((path, created)), 0)[1],
        )

    def test_fresh_token_uploads_without_login(self):
        created = datetime.now(UTC) - timedelta(days=1)
        _write_token(self.token_path, created)

        assert reauth() == 0
        assert len(self.pushed) == 1
        assert self.pushed[0][0] == self.token_path

    def test_token_near_the_wall_forces_a_login(self, monkeypatch):
        """Inside the last 12 hours, uploading buys almost nothing -- the
        secret would expire before the next daily run could use it, so a
        real login is worth the interruption."""
        created = datetime.now(UTC) - timedelta(days=TOKEN_LIFETIME_DAYS, hours=-2)
        _write_token(self.token_path, created)

        # Reaching the browser flow raises via the no_browser fixture, which
        # reauth() catches and reports as a failed login -- that's the proof
        # it tried, rather than taking the upload shortcut.
        assert reauth() == 1
        assert self.pushed == []

    def test_force_logs_in_even_when_valid(self):
        _write_token(self.token_path, datetime.now(UTC))

        assert reauth(force=True) == 1  # no_browser makes the login fail
        assert self.pushed == []

    def test_local_only_never_uploads(self):
        _write_token(self.token_path, datetime.now(UTC))

        assert reauth(local_only=True) == 1  # login attempted, then fails
        assert self.pushed == []
