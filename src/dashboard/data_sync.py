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
and that arrives in two places, so this fetches both:

  * history/vol_history.db is committed, so a `git pull --ff-only`.
  * database/schwab_database.db is not. It left Git LFS on 2026-11-22 --
    LFS stores a complete new copy per push, which took the quota to 9.01GB
    of an included 10GB -- and is now a release asset. See
    src/chain_release.py.

`git pull --ff-only` and nothing else: no user input reaches the command
line, and fast-forward-only means this can never create a merge commit or
rewrite anything. It refuses outright when tracked files are dirty, because
vol_history.db IS tracked and silently discarding local changes to it is how
a day of recovered data was lost once already. The message names the command
to drop them rather than running it: nine times in ten the local copy is a
stale artefact of something like a half-finished live pull, but the tenth is
a day of collection that exists nowhere else, and this code cannot tell
which. The chain database is no longer in that list because it is no longer
tracked -- which also retires the refusal that made this button unusable
from a branch that had diverged from main.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from src import chain_release

_PROJECT_ROOT = Path(__file__).parent.parent.parent

# The pull now moves 2.6MB of metric history and nothing else, so this no
# longer has to cover a multi-hour transfer. It used to be 600s to allow for
# the ~658MB chain database coming down through Git LFS -- which a 600s
# budget could not actually cover, and the overrun left an orphaned git
# process holding .git/index.lock for hours.
_TIMEOUT_SECONDS = 120

# Tracked files a pull would overwrite. Checked explicitly rather than
# relying on git's own refusal so the message can say which file and why.
# The chain database is absent deliberately: it is not tracked any more, so
# git can neither report it dirty nor overwrite it.
_TRACKED_DATA = ("history/vol_history.db",)

_CHAIN_DB = "database/schwab_database.db"


@dataclass
class _ChainOutcome:
    changed: bool
    note: str | None = None


def _refresh_chain() -> _ChainOutcome:
    """Bring the local chain database up to the published asset.

    Skipped when the local file already matches the asset's length. The
    chain table only ever grows, so equal sizes mean there is nothing new,
    and this is a ~658MB transfer not to start without a reason. Every
    uncertainty resolves toward downloading -- a missing local file, an
    unreadable one, a server that will not give a length -- because being
    wrong about "nothing changed" is silent and persists, while being wrong
    about "something changed" costs one transfer.

    A failure here is reported, not raised. The history pull may well have
    succeeded, and "most of your data arrived" is a different message from
    "sync failed".
    """
    destination = _PROJECT_ROOT / _CHAIN_DB
    local = destination.stat().st_size if destination.exists() else None
    if local is not None and local == chain_release.remote_size():
        return _ChainOutcome(changed=False)
    try:
        chain_release.download(destination)
    except chain_release.ChainDownloadError as exc:
        return _ChainOutcome(changed=False, note=f"the chain database did not update ({exc})")
    return _ChainOutcome(changed=True)


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
            message=f"Sync timed out after {_TIMEOUT_SECONDS // 60} minutes; try again.",
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
    # Whatever the cached answer was, this pull has just invalidated it --
    # otherwise the banner keeps offering data the user has already taken,
    # for as long as the TTL lasts.
    reset_upstream_cache()

    # The second half of the data. Reported together with the pull because
    # the user asked one question -- "show me what was collected" -- and
    # which file arrived over which protocol is not their concern. Timed
    # together for the same reason.
    chain = _refresh_chain()
    elapsed = time.monotonic() - started

    if before == after and not chain.changed:
        return SyncResult(
            changed=False,
            message=(
                f"Already up to date, but {chain.note}"
                if chain.note
                else "Already up to date -- you have everything the pipeline has collected."
            ),
            before=before,
            after=after,
            seconds=round(elapsed, 1),
        )

    return SyncResult(
        changed=True,
        message=(
            f"Synced the metric history, but {chain.note}"
            if chain.note
            else "Synced. New data is live."
        ),
        before=before,
        after=after,
        seconds=round(elapsed, 1),
    )


# ---------------------------------------------------------------------------
# Is there anything to sync?
# ---------------------------------------------------------------------------
#
# The dashboard reads a file on disk, but the data arrives in a git commit.
# Nothing connected those two facts on screen: the Overview reported how old
# the snapshot was, which is the same "2d ago" whether the pipeline failed or
# whether it succeeded and nobody pulled. Twice in one week the second case
# was diagnosed as the first, and the only visible difference was in a
# terminal.
#
# So the page needs to know whether origin has data this checkout does not.
#
# This is deliberately `git ls-remote` and not `git fetch`. ls-remote asks the
# remote for one ref and downloads no objects, which keeps it fast enough to
# call from a page load and -- the real reason -- keeps it read-only. A fetch
# writes objects and moves remote-tracking refs in the user's repository, and
# a GET that mounts on every page should not be quietly mutating the thing it
# is reporting on.
#
# The cost of staying read-only is that the exact count is not always
# knowable: counting commits requires having them. When a previous fetch left
# the objects behind the count is exact; otherwise the honest answer is "there
# is something new, I cannot say how much", and the UI says that instead of
# inventing a number.

_UPSTREAM_TTL_SECONDS = 180
_UPSTREAM_TIMEOUT_SECONDS = 10
_UPSTREAM_REF = "refs/heads/main"


@dataclass
class UpstreamStatus:
    """What origin/main has that this checkout does not.

    `behind` is the count of commits, or None when it could not be
    determined without downloading objects. `has_updates` is the question
    the banner actually asks, and stays answerable either way.
    """

    has_updates: bool
    behind: int | None = None
    dirty: list[str] | None = None
    reason: str | None = None


_upstream_cache: tuple[float, UpstreamStatus] | None = None


def _remote_main_sha() -> tuple[str | None, str | None]:
    """(sha, error). One network round trip, no objects transferred."""
    try:
        result = _git("ls-remote", "origin", _UPSTREAM_REF, timeout=_UPSTREAM_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return None, "Could not reach origin (timed out)."
    except (OSError, FileNotFoundError) as exc:
        return None, f"Could not run git: {exc}"

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return None, f"Could not reach origin: {detail[-1] if detail else 'unknown git error'}"

    fields = result.stdout.split()
    if not fields:
        return None, "origin has no main branch."
    return fields[0], None


def _count_ahead_of_head(sha: str) -> int | None:
    """Commits in `sha` but not in HEAD, or None if the objects are absent.

    Checked with cat-file first because rev-list on an unknown commit fails
    with a message about a bad revision, which would read as a broken
    install rather than the ordinary "we have not fetched that yet".
    """
    if _git("cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        return None
    result = _git("rev-list", "--count", f"HEAD..{sha}")
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def upstream_status(*, force: bool = False) -> UpstreamStatus:
    """Whether origin/main holds data this checkout has not pulled.

    Cached briefly: several components mount this on one page load, and the
    answer changes at most once a weekday when the collection commits.
    """
    global _upstream_cache

    now = time.monotonic()
    if not force and _upstream_cache is not None:
        checked_at, cached = _upstream_cache
        if now - checked_at < _UPSTREAM_TTL_SECONDS:
            return cached

    dirty = dirty_tracked_data()
    sha, error = _remote_main_sha()
    if sha is None:
        # Offline is not a problem to report. The banner's job is to say
        # "there is new data"; it has no business showing a network error
        # for a check the user never asked for.
        status = UpstreamStatus(has_updates=False, dirty=dirty, reason=error)
        _upstream_cache = (now, status)
        return status

    head = _git("rev-parse", "HEAD").stdout.strip()
    if head and sha == head:
        status = UpstreamStatus(has_updates=False, behind=0, dirty=dirty)
    else:
        behind = _count_ahead_of_head(sha)
        # behind == 0 with differing SHAs means this checkout is ahead of
        # origin (local commits, or a branch that has moved on), not behind.
        # There is nothing to pull, so there is nothing to announce.
        status = UpstreamStatus(
            has_updates=behind is None or behind > 0,
            behind=behind,
            dirty=dirty,
        )

    _upstream_cache = (now, status)
    return status


def reset_upstream_cache() -> None:
    """Drop the cached answer -- call after a pull, which invalidates it."""
    global _upstream_cache
    _upstream_cache = None
