"""
src/dashboard/data_sync.py

Fetch the collected data the pipeline has already produced.

Why this replaced a live Schwab pull
------------------------------------
The dashboard's refresh control used to call Schwab directly. Once
data_loader started reading database/schwab_database.db -- the file the
daily workflow commits -- that stopped being the right operation, and
became an actively harmful one:

  * Schwab rotates the refresh token on every use. A successful local pull
    therefore invalidates the copy in SCHWAB_TOKEN_B64 and stops the next
    scheduled collection, so the button's *success* case broke the pipeline.
  * Outside market hours Schwab returns -999 sentinels, job.py correctly
    declines to overwrite good data with them, and nothing is written -- so
    the button's failure case did nothing at all.
  * The token on a developer's disk goes stale within a day anyway, because
    CI has been rotating it since. The usual outcome was a raw OAuth error.

What a user means by "refresh" now is "show me what the pipeline collected",
and that is a git pull. The data arrives already computed, already
validated, and without touching a credential.

`git pull --ff-only` and nothing else: no user input reaches the command
line, and fast-forward-only means this can never create a merge commit or
rewrite anything. It refuses outright when tracked files are dirty, because
the two database files ARE tracked and silently discarding local changes to
them is how a day of recovered data was lost once already. The message names
the command to drop them rather than running it: nine times in ten the local
copy is a stale artefact of something like a half-finished live pull, but the
tenth is a day of collection that exists nowhere else, and this code cannot
tell which.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent.parent

# Generous: the chain database is ~370MB in Git LFS, so the first pull after
# a new snapshot downloads it in full. A tight timeout would abort a sync
# that was working, leaving a half-fetched LFS object behind.
_TIMEOUT_SECONDS = 600

# Tracked files a pull would overwrite. Checked explicitly rather than
# relying on git's own refusal so the message can say which file and why.
_TRACKED_DATA = ("database/schwab_database.db", "history/vol_history.db")


@dataclass
class SyncResult:
    changed: bool
    message: str
    before: str | None = None
    after: str | None = None
    seconds: float | None = None


def _git(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _head() -> str | None:
    result = _git("rev-parse", "--short", "HEAD")
    return result.stdout.strip() or None if result.returncode == 0 else None


def dirty_tracked_data() -> list[str]:
    """Tracked database files with uncommitted local modifications."""
    result = _git("status", "--porcelain", "--", *_TRACKED_DATA)
    if result.returncode != 0:
        return []
    return [line[3:].strip() for line in result.stdout.splitlines() if line.strip()]


def pull_latest() -> SyncResult:
    """Fast-forward to origin/main, bringing down whatever has been collected.

    Returns a SyncResult rather than raising for the expected outcomes --
    already current, local changes present, no network -- because each is
    something the UI should state plainly rather than render as an error.
    """
    if dirty := dirty_tracked_data():
        return SyncResult(
            changed=False,
            message=(
                f"Local changes to {', '.join(dirty)} would be overwritten. "
                "These are generated data files that the pipeline owns, so the local "
                f"copy is almost certainly the one to drop: git checkout -- {' '.join(dirty)}"
            ),
        )

    before = _head()
    started = time.monotonic()
    try:
        result = _git("pull", "--ff-only", "origin", "main", timeout=_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return SyncResult(
            changed=False,
            message=f"Sync timed out after {_TIMEOUT_SECONDS // 60} minutes -- the chain database is large; try again.",
        )
    except (OSError, FileNotFoundError) as exc:
        return SyncResult(changed=False, message=f"Could not run git: {exc}")

    elapsed = time.monotonic() - started
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return SyncResult(
            changed=False,
            message=f"Sync failed: {detail[-1] if detail else 'unknown git error'}",
            seconds=round(elapsed, 1),
        )

    after = _head()
    if before == after:
        return SyncResult(
            changed=False,
            message="Already up to date -- you have everything the pipeline has collected.",
            before=before,
            after=after,
            seconds=round(elapsed, 1),
        )

    return SyncResult(
        changed=True,
        message="Synced. New data is live.",
        before=before,
        after=after,
        seconds=round(elapsed, 1),
    )
