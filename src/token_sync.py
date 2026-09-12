"""
src/token_sync.py

Persists the local token.json back to the SCHWAB_TOKEN_B64 GitHub Actions
secret after every scheduled run, so the *next* run starts from a live
refresh token instead of the one frozen in the secret whenever it was first
created.

Why this exists
----------------
Schwab's refresh token is only valid ~7 days and schwab-py rotates it (a
fresh refresh token comes back on every use, invalidating the previous one).
The GitHub Actions runner's disk is ephemeral, so the token.json schwab-py
refreshes in place during a run is discarded when the job finishes -- the
next scheduled run started again from SCHWAB_TOKEN_B64's original bytes.
Once Schwab's rotation invalidated that frozen refresh token (which it does,
typically within a week), every subsequent run failed with `invalid_grant`
until someone manually reran the OAuth browser flow and re-uploaded the
secret by hand -- this is the "I cannot re-auth every day" problem.

This script closes that loop: after every scheduled run, it reads whatever
token.json is on disk (possibly refreshed by schwab-py during that run) and
pushes it back to the SCHWAB_TOKEN_B64 secret via the GitHub API. As long as
the workflow runs at least once within Schwab's ~7-day refresh-token window
(it runs daily), the refresh token should never go stale from disuse again.
Manual re-auth is then only needed if Schwab revokes the grant outright
(e.g. the registered app's API access is pulled) or the workflow doesn't run
at all for more than ~7 days straight (see pipeline-heartbeat.yml, which
already watches for exactly that).

GitHub secrets are write-only (there is no "read the current value" API),
so this always re-encrypts and PUTs the current on-disk token rather than
diffing against what's already stored -- an unconditional PUT of unchanged
content is a harmless no-op.

Requires a GitHub PAT with permission to manage this repo's Actions secrets
(classic PAT: 'repo' scope; fine-grained PAT: 'Secrets' repository
permission set to Read and write) stored as the SECRETS_PAT repo secret --
the default GITHUB_TOKEN cannot manage Actions secrets.

Outside CI (no GITHUB_ACTIONS env var) this stays a harmless no-op when
SECRETS_PAT/GITHUB_REPOSITORY aren't set, so it's safe to call locally.
*Inside* CI the same situation is fatal instead -- see
TokenSyncMisconfiguredError for why a quiet skip there proved actively
harmful. It also refuses to push a token file that isn't valid JSON, so a
failed auth can't corrupt a recoverable secret into an unrecoverable one.

Usage:
    python -m src.token_sync
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import httpx
from nacl import encoding, public

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("token_sync")

SECRET_NAME = "SCHWAB_TOKEN_B64"
API_BASE = "https://api.github.com"


class TokenSyncMisconfiguredError(Exception):
    """Raised when this is running in CI but can't do its job -- either
    SECRETS_PAT isn't configured, or the on-disk token is unusable.

    Deliberately fatal rather than a warning: a silent no-op here is exactly
    how the refresh token was allowed to expire five separate times. The sync
    step reported success on every run while doing nothing at all, so the
    only visible symptom was the pipeline dying about a week later for
    apparently unrelated reasons. Failing the step makes the actual cause
    obvious on the run that causes it, not seven days downstream."""


def _encrypt_secret(public_key_b64: str, secret_value: str) -> str:
    """Encrypt secret_value per GitHub's 'update a repo secret' API (libsodium sealed box)."""
    public_key = public.PublicKey(public_key_b64.encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(public_key)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


def sync_token_to_secret(token_path: Path | None = None) -> bool:
    """
    Push the current on-disk token.json to the SCHWAB_TOKEN_B64 GitHub secret.

    Returns True if the secret was updated, False if skipped (no PAT
    configured, not running against a GitHub repo, or no token file to sync).
    """
    pat = os.environ.get("SECRETS_PAT")
    repo = os.environ.get("GITHUB_REPOSITORY")  # "owner/repo", auto-set in Actions
    in_ci = os.environ.get("GITHUB_ACTIONS") == "true"
    if not pat or not repo:
        if in_ci:
            raise TokenSyncMisconfiguredError(
                "Running in GitHub Actions but SECRETS_PAT isn't configured, so the "
                "refreshed token can't be written back to the SCHWAB_TOKEN_B64 secret. "
                "Every run will keep working until Schwab rotates the refresh token out "
                "(~7 days) and then the pipeline will fail with invalid_grant. Add a PAT "
                "with Actions 'Secrets: write' permission as the SECRETS_PAT repo secret."
            )
        logger.info(
            "SECRETS_PAT or GITHUB_REPOSITORY not set -- skipping token sync "
            "(expected outside CI)."
        )
        return False

    path = token_path or Path(os.environ.get("TOKEN_PATH", "token.json"))
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    if not path.exists():
        logger.warning("No token file at %s -- nothing to sync.", path)
        return False

    # Validate before pushing. This step runs with `if: always()` so that a
    # genuinely refreshed token still gets saved when the snapshot failed for
    # an unrelated reason -- but that also means it can run right after a
    # failed auth, when whatever is on disk may be truncated or half-written.
    # Pushing that would overwrite a merely-expired secret with an undecodable
    # one, turning a self-healing problem into a deadlock: the next run then
    # fails at "Restore token.json from secret" before the pipeline can run,
    # so this sync never gets another chance to repair it. Refusing to push
    # unusable content is what keeps `if: always()` safe.
    raw = path.read_bytes()
    try:
        json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        message = (
            f"Refusing to sync {path}: it is {len(raw)} bytes that aren't valid JSON ({exc}). "
            "Pushing this would replace a recoverable SCHWAB_TOKEN_B64 with an undecodable one."
        )
        if in_ci:
            raise TokenSyncMisconfiguredError(message) from exc
        logger.warning(message)
        return False

    token_b64 = base64.b64encode(raw).decode("utf-8")

    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(timeout=30.0) as client:
        pk_resp = client.get(f"{API_BASE}/repos/{repo}/actions/secrets/public-key", headers=headers)
        pk_resp.raise_for_status()
        pk = pk_resp.json()

        encrypted_value = _encrypt_secret(pk["key"], token_b64)

        put_resp = client.put(
            f"{API_BASE}/repos/{repo}/actions/secrets/{SECRET_NAME}",
            headers=headers,
            json={"encrypted_value": encrypted_value, "key_id": pk["key_id"]},
        )
        put_resp.raise_for_status()

    logger.info("Synced token.json back to the %s secret.", SECRET_NAME)
    return True


if __name__ == "__main__":
    sync_token_to_secret()
