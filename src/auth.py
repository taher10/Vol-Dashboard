"""
src/auth.py

SchwabAuth — wraps Schwab API OAuth 2.0 via schwab-py.

Usage
-----
    auth = SchwabAuth.from_env()          # reads config.ini first, then env vars

    # First time only (opens browser):
    client = auth.authenticate()

    # Every subsequent run:
    client = auth.get_client()
"""

from __future__ import annotations

import base64
import binascii
import configparser
import json
import os
from pathlib import Path

import schwab
from dotenv import load_dotenv
import httpx

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config.ini"


class InvalidTokenSecretError(ValueError):
    """Raised by write_token_from_base64 when SCHWAB_TOKEN_B64 is missing/empty
    or doesn't decode to a valid token.json -- see that function's docstring
    for why this needs to fail loudly rather than write a broken file."""


def write_token_from_base64(b64_token: str, token_path: str | Path | None = None) -> Path:
    """
    Decode a base64-encoded token.json (produced locally via `base64 -i
    token.json` after a one-time `--first-time` OAuth run) and write it to
    disk, but only if no token file exists there yet.

    For hosts with ephemeral storage (e.g. Streamlit Community Cloud, a fresh
    GitHub Actions checkout), token.json from a local first-time auth doesn't
    survive a redeploy/cold start. Stashing its base64 contents as a secret
    and calling this on startup re-materializes it without needing a
    browser-based OAuth flow on the server itself. Never overwrites an
    existing token -- schwab-py refreshes it in place, so a live token on
    disk is newer than the secret.

    Validates eagerly (empty input, malformed base64, non-JSON content)
    instead of writing whatever it's given: an empty or misconfigured
    SCHWAB_TOKEN_B64 secret used to silently produce a 0-byte token.json,
    which only surfaced several steps later as a confusing
    `json.JSONDecodeError` deep in schwab-py's own token-loading code,
    with nothing pointing back at the actual cause.
    """
    if not b64_token or not b64_token.strip():
        raise InvalidTokenSecretError(
            "SCHWAB_TOKEN_B64 is empty. Generate it locally with "
            "`base64 -i token.json | tr -d '\\n'` (after a one-time "
            "`python -m src.job --first-time` run) and set it as a secret."
        )

    try:
        decoded = base64.b64decode(b64_token, validate=True)
    except binascii.Error as exc:
        raise InvalidTokenSecretError(
            f"SCHWAB_TOKEN_B64 is not valid base64 ({exc}). Re-generate it with "
            "`base64 -i token.json | tr -d '\\n'` and make sure the whole value "
            "was copied with no extra whitespace/newlines."
        ) from exc

    try:
        json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise InvalidTokenSecretError(
            f"SCHWAB_TOKEN_B64 decoded to {len(decoded)} bytes that aren't valid JSON ({exc}). "
            "It should decode to your token.json's exact contents -- re-generate it with "
            "`base64 -i token.json | tr -d '\\n'`."
        ) from exc

    path = Path(token_path) if token_path else Path(os.environ.get("TOKEN_PATH", "token.json"))
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(decoded)
    return path


class SchwabAuth:
    """Manages Schwab OAuth credentials and returns an authenticated client."""

    def __init__(
        self,
        api_key: str,
        app_secret: str,
        callback_url: str = "https://oauth.pstmn.io/v1/callback",
        token_path: str | Path = "token.json",
        timeout: float = 120.0,
    ) -> None:
        if not api_key or not app_secret:
            raise ValueError("api_key and app_secret must not be empty.")
        self.api_key = api_key
        self.app_secret = app_secret
        self.callback_url = callback_url
        self.timeout = timeout
        self.token_path = Path(token_path) if not Path(token_path).is_absolute() else Path(token_path)
        # Resolve relative paths against project root
        if not self.token_path.is_absolute():
            self.token_path = _PROJECT_ROOT / self.token_path

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "SchwabAuth":
        """
        Instantiate credentials from local config.ini, with env var fallback.

        Priority:
          1) [schwab] section in config.ini
          2) Environment variables (optionally from .env)
        """
        load_dotenv(env_file or _PROJECT_ROOT / ".env")

        cfg = configparser.ConfigParser()
        cfg.read(_DEFAULT_CONFIG_PATH)
        schwab_cfg = cfg["schwab"] if cfg.has_section("schwab") else {}

        api_key = schwab_cfg.get("api_key", os.environ.get("SCHWAB_API_KEY", ""))
        app_secret = schwab_cfg.get("app_secret", os.environ.get("SCHWAB_APP_SECRET", ""))
        callback_url = schwab_cfg.get(
            "callback_url",
            os.environ.get("SCHWAB_CALLBACK_URL", "https://oauth.pstmn.io/v1/callback"),
        )
        token_path = schwab_cfg.get("token_path", os.environ.get("TOKEN_PATH", "token.json"))
        timeout = float(schwab_cfg.get("timeout", os.environ.get("SCHWAB_TIMEOUT", "120")))

        return cls(api_key, app_secret, callback_url, token_path, timeout=timeout)

    def authenticate(self) -> schwab.client.Client:
        """
        Run the OAuth browser-based flow (first-time setup).
        Opens a browser, prompts you to paste back the redirect URL,
        and saves the token to disk. Run once per environment.
        """
        client = schwab.auth.client_from_manual_flow(
            api_key=self.api_key,
            app_secret=self.app_secret,
            callback_url=self.callback_url,
            token_path=str(self.token_path),
        )
        client.session.timeout = httpx.Timeout(self.timeout)
        print(f"[SchwabAuth] Token saved to: {self.token_path}")
        return client

    def get_client(self) -> schwab.client.Client:
        """
        Load a saved token from disk and return an authenticated client.
        The token is refreshed automatically when expired.
        Raises FileNotFoundError if no token exists — call authenticate() first.
        """
        if not self.token_path.exists():
            raise FileNotFoundError(
                f"No token at '{self.token_path}'. Run authenticate() first."
            )
        client = schwab.auth.client_from_token_file(
            token_path=str(self.token_path),
            api_key=self.api_key,
            app_secret=self.app_secret,
        )
        client.session.timeout = httpx.Timeout(self.timeout)
        return client
