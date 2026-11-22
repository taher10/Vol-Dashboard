"""
src/chain_release.py

Fetch the raw options-chain database from its GitHub release asset.

Why the database is not in git any more
---------------------------------------
database/schwab_database.db was tracked via Git LFS from 2026-08-20, which
fixed the 100MB per-file push limit and created a slower problem in its
place. LFS cannot delta-compress a SQLite file, so every nightly push stored
a COMPLETE NEW COPY: 58 commits touching a file that grew to 658MB took the
quota to 9.01GB of an included 10GB, while the daily growth rate (12.2MB per
session) never changed once. Storage is a stock, not a flow -- it does not
reset monthly, and deleting branches or rewriting history does not reclaim
it. With a $0 LFS budget the overage is refused rather than billed, so the
failure at 10GB is `git push` being rejected in daily-snapshot.yml: data
collected, then unsavable, which for a source that serves current chains
only is a permanent hole.

Release assets are not metered against the LFS quota, old ones can actually
be deleted, and the repo already runs this pattern -- publish-demo.yml has
been replacing a 105MB asset on a fixed tag for months, and render.yaml
curls that stable URL. So the chain database moved to the same arrangement.

The LFS objects remain in git history, so any pre-migration state is still
recoverable with `git lfs pull` at an old commit. Nothing was discarded;
only the growth stopped.

Why urllib and not httpx
------------------------
httpx is in requirements.txt and used by src/notify.py and src/token_sync.py,
but it is deliberately absent from requirements-demo.txt -- the demo installs
a reduced set so the machine CANNOT reach Schwab or Anthropic even if a route
gate failed open, and tests/test_demo_mode.py boots the app with those
libraries blocked to keep it that way. This module is reachable from
data_sync, so a module-scope `import httpx` here would fail that test and the
demo deploy. Downloading a public URL does not need a third-party client.

Why verify before replacing
---------------------------
The download target is the only copy of four months of chains on a given
machine. A truncated transfer, a 404 body or an HTML error page must not be
allowed to land on top of it. So the bytes go to a temporary file beside the
destination, get opened as SQLite and counted, and only a file that passes
is moved into place -- with os.replace(), which is atomic within a
filesystem, so a reader never sees a half-written database.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPOSITORY = "taher10/Vol-Dashboard"
TAG = "chain-latest"
ASSET_NAME = "schwab_database.db"
ASSET_URL = f"https://github.com/{REPOSITORY}/releases/download/{TAG}/{ASSET_NAME}"

# A floor against pointers and error pages, not a judgement about the data.
# An LFS pointer is ~134 bytes and a GitHub error page a few KB; the real
# database was 658MB at migration and only grows.
_MIN_BYTES = 100_000_000

_CHUNK = 1 << 20
_TIMEOUT_SECONDS = 300

# Generic on purpose: this identifies the program, not the person running it.
_USER_AGENT = "vol-dashboard"


class ChainDownloadError(RuntimeError):
    """The asset could not be fetched, or what arrived was not the database."""


def row_count(path: Path | str) -> int:
    """Contract rows in a chain database, or raise if it is not one.

    Read-only URI so this can never create an empty file at a mistyped
    path -- sqlite3.connect() on a missing path would otherwise produce a
    valid, empty database and make "is it there?" answer yes.
    """
    connection = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM options").fetchone()[0])
    finally:
        connection.close()


def verify(path: Path | str, *, min_rows: int = 1) -> int:
    """Check a file really is the chain database. Returns its row count."""
    path = Path(path)
    if not path.exists():
        raise ChainDownloadError(f"{path} does not exist")
    size = path.stat().st_size
    if size < _MIN_BYTES:
        raise ChainDownloadError(
            f"{path} is {size:,} bytes, below the {_MIN_BYTES:,} floor -- "
            "an LFS pointer is ~134 bytes and an HTML error page a few KB, "
            "so this is almost certainly not the database."
        )
    try:
        rows = row_count(path)
    except sqlite3.DatabaseError as exc:
        raise ChainDownloadError(f"{path} is not a readable SQLite database ({exc})") from exc
    if rows < min_rows:
        raise ChainDownloadError(f"{path} holds {rows:,} contract rows, fewer than the required {min_rows:,}")
    return rows


def _existing_row_count(path: Path) -> int | None:
    """Rows in whatever is already at `path`, or None if it is unusable.

    Unusable covers "absent" and "corrupt" alike, because both mean the same
    thing here: there is nothing worth protecting, so a download may proceed.
    """
    if not path.exists():
        return None
    try:
        return row_count(path)
    except sqlite3.DatabaseError:
        return None


def remote_size(url: str = ASSET_URL, *, timeout: int = 30) -> int | None:
    """Byte length of the published asset, or None if it cannot be learned.

    Used to decide whether a 658MB download is worth starting. The chain
    table grows every session, so a size equal to the local file's means
    there is nothing new -- a far more direct signal than "did git bring a
    commit", which is a different question and gets a different answer
    whenever the commit and the publish disagree about how a run went.

    None on any difficulty, so the caller downloads rather than skipping:
    being wrong about "nothing changed" is silent and lasting, being wrong
    about "something changed" costs one transfer.
    """
    try:
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
        return int(length) if length is not None else None
    except (urllib.error.URLError, ValueError, OSError):
        return None


def download(
    dest: Path | str,
    url: str = ASSET_URL,
    *,
    allow_fewer_rows: bool = False,
    timeout: int = _TIMEOUT_SECONDS,
) -> int:
    """Fetch the asset to `dest`, verifying before it replaces anything.

    Returns the downloaded database's contract-row count.

    By default this refuses to replace a local database with one holding
    fewer rows. The chain table is append-only, so a smaller remote copy
    means something went wrong upstream rather than that history was
    revised -- and the local file may be the only copy of a session.
    Pass allow_fewer_rows=True for a deliberate rollback.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Beside the destination, not in /tmp: os.replace() is only atomic within
    # a single filesystem, and on this project /tmp routinely is not one.
    temporary = dest.with_name(dest.name + ".download")

    try:
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response, open(temporary, "wb") as handle:
                while chunk := response.read(_CHUNK):
                    handle.write(chunk)
        except urllib.error.HTTPError as exc:
            hint = (
                f" -- no asset named {ASSET_NAME} on the {TAG} release. "
                "Seed it before relying on this (see .github/workflows/publish-chain.yml "
                "in the migration PR)."
                if exc.code == 404
                else ""
            )
            raise ChainDownloadError(f"{url} returned HTTP {exc.code}{hint}") from exc
        except urllib.error.URLError as exc:
            raise ChainDownloadError(f"Could not reach {url} ({exc.reason})") from exc

        downloaded = verify(temporary)

        existing = _existing_row_count(dest)
        if existing is not None and downloaded < existing and not allow_fewer_rows:
            raise ChainDownloadError(
                f"Refusing to replace {dest}: it holds {existing:,} contract rows and the "
                f"download holds {downloaded:,}. The chain table only ever grows, so a "
                "smaller asset means something went wrong upstream."
            )

        os.replace(temporary, dest)
        return downloaded
    finally:
        # Never leave a partial file behind to be mistaken for the database,
        # or to occupy 600MB after a failure nobody noticed.
        temporary.unlink(missing_ok=True)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[2])
    sub = parser.add_subparsers(dest="command", required=True)

    get = sub.add_parser("download", help="fetch the asset, verifying before it replaces anything")
    get.add_argument("dest", nargs="?", default="database/schwab_database.db")
    get.add_argument("--url", default=ASSET_URL)
    get.add_argument("--allow-fewer-rows", action="store_true")

    check = sub.add_parser("verify", help="check a local file really is the chain database")
    check.add_argument("path", nargs="?", default="database/schwab_database.db")
    check.add_argument("--min-rows", type=int, default=1)

    args = parser.parse_args(argv)
    try:
        if args.command == "download":
            rows = download(args.dest, args.url, allow_fewer_rows=args.allow_fewer_rows)
            print(f"downloaded {args.dest}: {rows:,} contract rows")
        else:
            rows = verify(args.path, min_rows=args.min_rows)
            print(f"{args.path}: {rows:,} contract rows")
    except ChainDownloadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
