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

import configparser
import os
from pathlib import Path

import schwab
from dotenv import load_dotenv
import httpx

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config.ini"


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
