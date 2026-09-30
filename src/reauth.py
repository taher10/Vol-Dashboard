"""
src/reauth.py

The once-a-week button: re-authorise with Schwab and push the new token
straight to the GitHub secret, in one command.

Why this exists
---------------
Schwab's refresh token dies exactly 7 days after the *browser* authorisation
and nothing can extend it. schwab-py's own `easy_client` default says the
same thing in code -- `max_token_age=60*60*24*6.5`, after which it discards
the token and forces a fresh login flow rather than a refresh. The
`creation_timestamp` it stores "does not change when the token is updated"
(schwab/auth.py), which is precisely how a token that is refreshed
successfully every single day still dies on day 7.

src/token_sync.py solves a real but *different* problem -- the runner's disk
is ephemeral, so without it the rotated token is thrown away and the next run
starts from a token Schwab already invalidated. It cannot move the 7-day
wall, and this project spent a week treating each scheduled death as a fresh
incident because the distinction wasn't drawn.

So the browser step is unavoidable. What was avoidable is everything that
used to surround it: run the flow, find token.json, base64 it, strip the
newline, open the repo settings, paste into SCHWAB_TOKEN_B64 -- six steps
with an ordering trap in the middle. Encoding the secret *before* a later
local run rotates the token leaves the secret holding an already-invalidated
token, which fails identically to an expired one and is indistinguishable
from it in the logs.

This collapses that to: run this, log in, done. The secret is written from
the same bytes that were just created, so the ordering trap cannot happen.

Two flows, picked automatically from the configured callback URL:

  * A loopback callback (127.0.0.1 / localhost) uses client_from_login_flow:
    schwab-py runs a throwaway local HTTPS server, catches the redirect, and
    there is nothing to copy or paste.
  * Anything else -- e.g. the https://oauth.pstmn.io/v1/callback many people
    start with -- must use the manual flow, where Schwab redirects to a page
    whose URL you paste back once.

Changing the Schwab app's callback URL to https://127.0.0.1:8182 upgrades
this to the zero-paste flow with no change here.

Usage:
    python -m src.reauth              # re-auth, then update the GitHub secret
    python -m src.reauth --local-only # re-auth only, leave the secret alone
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import schwab

from src.auth import SchwabAuth
from src.token_sync import SECRET_NAME, sync_token_to_secret

# Schwab invalidates the refresh token 7 days after the browser authorisation.
# Reported here so the next re-auth is a calendar entry rather than a surprise.
TOKEN_LIFETIME_DAYS = 7


def _is_loopback(callback_url: str) -> bool:
    """True when schwab-py can catch the redirect itself with a local server."""
    host = (urlparse(callback_url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def _repo_from_git_remote() -> str | None:
    """Derive "owner/repo" from origin, so GITHUB_REPOSITORY (which only
    Actions sets automatically) doesn't have to be configured by hand."""
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=_PROJECT_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    url = url.removesuffix(".git")
    if url.startswith("git@"):  # git@github.com:owner/repo
        url = url.partition(":")[2]
    else:  # https://github.com/owner/repo
        url = urlparse(url).path.lstrip("/")
    return url if url.count("/") == 1 else None


def _token_created_at(token_path: Path) -> datetime | None:
    try:
        stamp = json.loads(token_path.read_bytes()).get("creation_timestamp")
    except (OSError, json.JSONDecodeError):
        return None
    return datetime.fromtimestamp(stamp, UTC) if stamp else None


def reauth(local_only: bool = False, force: bool = False) -> int:
    """Returns a process exit code: 0 the pipeline is good to go, 1 it isn't."""
    auth = SchwabAuth.from_env()
    token_path = Path(auth.token_path)
    before = _token_created_at(token_path)

    # A still-valid token doesn't need a new browser login -- it needs to
    # reach the secret. That was the actual failure on 2026-09-23: the
    # browser flow was completed at 05:48 UTC and token.json was good for a
    # week, but the upload never happened, so every workflow kept failing on
    # the Sep 10 token while a working one sat on disk. Logging in again to
    # fix an upload problem is wasted effort, so check before asking.
    if before is not None and not force and not local_only:
        remaining = (before + timedelta(days=TOKEN_LIFETIME_DAYS)) - datetime.now(UTC)
        if remaining > timedelta(hours=12):
            print(
                f"Your token is still valid -- created {before:%Y-%m-%d %H:%M UTC}, "
                f"{remaining.days}d {remaining.seconds // 3600}h left.\n"
                "No browser login needed; uploading it to GitHub instead.\n"
                "(Use --force to log in again anyway.)\n"
            )
            return _push_to_secret(token_path, before)

    loopback = _is_loopback(auth.callback_url)
    print(f"Callback: {auth.callback_url}")
    if loopback:
        print("A browser window will open. Log in to Schwab and approve; nothing to paste.\n")
    else:
        print(
            "A browser window will open. Log in to Schwab and approve, then copy the\n"
            "FULL URL of the page you land on and paste it back here when prompted.\n"
        )

    try:
        if loopback:
            client = schwab.auth.client_from_login_flow(
                api_key=auth.api_key,
                app_secret=auth.app_secret,
                callback_url=auth.callback_url,
                token_path=str(token_path),
            )
        else:
            client = schwab.auth.client_from_manual_flow(
                api_key=auth.api_key,
                app_secret=auth.app_secret,
                callback_url=auth.callback_url,
                token_path=str(token_path),
            )
        client.session.timeout = auth.timeout  # plain number -- see src/auth.py
    except Exception as exc:  # noqa: BLE001 -- any failure here is the answer
        print(f"\nERROR: the Schwab login flow did not complete: {type(exc).__name__}: {exc}")
        print("Nothing was changed. The old token is still in place.")
        return 1

    # The flow can appear to succeed without replacing the token -- the exact
    # failure that kept this pipeline down for a week, when an old token.json
    # was re-encoded and re-uploaded. Verify against the file rather than
    # trusting that a client object came back.
    after = _token_created_at(token_path)
    if after is None:
        print(f"\nERROR: no readable token at {token_path} after the flow.")
        return 1
    if before is not None and after <= before:
        print(
            f"\nERROR: {token_path} was not replaced -- creation_timestamp is still "
            f"{after:%Y-%m-%d %H:%M UTC}.\nThe login flow did not complete. The secret "
            "was left alone, because uploading this token would fail exactly as before."
        )
        return 1

    print(f"\nNew token created {after:%Y-%m-%d %H:%M UTC}")

    if local_only:
        print(f"\n--local-only: {SECRET_NAME} was NOT updated, so GitHub Actions still "
              "has the old token and the daily collection will keep failing.")
        return 0

    return _push_to_secret(token_path, after)


def _push_to_secret(token_path: Path, created_at: datetime) -> int:
    """Upload token_path's current bytes to SCHWAB_TOKEN_B64.

    Always reads the file rather than taking the token in memory, so the
    secret holds exactly what is on disk -- the ordering trap this module's
    docstring describes cannot occur.
    """
    import os

    expires = created_at + timedelta(days=TOKEN_LIFETIME_DAYS)

    if not os.environ.get("GITHUB_REPOSITORY"):
        if repo := _repo_from_git_remote():
            os.environ["GITHUB_REPOSITORY"] = repo

    if not os.environ.get("SECRETS_PAT"):
        print(
            f"{SECRET_NAME} was NOT updated: SECRETS_PAT isn't set locally.\n\n"
            "Add it to .env once (.env is gitignored) and this becomes fully automatic:\n\n"
            "    SECRETS_PAT=ghp_yourtokenhere\n\n"
            "It needs Actions 'Secrets: write' on this repo -- the same permission the\n"
            "SECRETS_PAT repo secret already has, so you can reuse that PAT if you still\n"
            "have it, or make a new one.\n\n"
            f"Until then the daily collection keeps failing, because {SECRET_NAME} still\n"
            "holds the old token even though this machine has a good one."
        )
        return 1

    try:
        synced = sync_token_to_secret(token_path)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: pushing {SECRET_NAME} failed: {type(exc).__name__}: {exc}")
        print("GitHub Actions still has the old token. Fix the PAT and re-run.")
        return 1

    if not synced:
        print(f"ERROR: {SECRET_NAME} was not updated (no repo configured?).")
        return 1

    print(f"{SECRET_NAME} updated -- GitHub Actions will use this token.")
    print(f"Valid until {expires:%Y-%m-%d %H:%M UTC}.")
    print(f"\nDone. Next re-auth due {expires:%a %d %b}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-authorise with Schwab and update the GitHub secret.")
    parser.add_argument(
        "--local-only", action="store_true",
        help="Refresh token.json but don't touch the GitHub secret.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Run the browser login even if the current token is still valid.",
    )
    args = parser.parse_args()
    return reauth(local_only=args.local_only, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
