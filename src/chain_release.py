"""
src/chain_release.py

Move the chain store between this machine and its GitHub release assets.

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
curls that stable URL.

Why one asset per month and not one asset for everything
--------------------------------------------------------
Moving the single 658MB file to a release asset only moved the wall. GitHub
caps an asset at 2GB, which at 12.2MB a session is about 114 sessions --
roughly 5.4 months of knowing the date it breaks. That is the thing worth
fixing, not the thing worth relocating.

So the store is compressed (13.5x, exactly -- see src/chain_codec.py) and
split by calendar month, and each month is its own asset:

    schwab_database.db      ~1 MB    index + prices + precomputed metrics
    chains-2026-07.db       ~2 MB
    chains-2026-08.db      ~10 MB
    ...

Nothing here has a ceiling that arrives. A month's partition is bounded by
the length of a month, not by the age of the project; the count of assets
grows by twelve a year against no limit worth planning around; and a
nightly run transfers the two newest partitions rather than the whole
archive, so the cost of a session stops depending on how many came before
it.

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
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

from src.schwab_database import (
    SchwabDatabase,
    resolve_db_path,
    resolve_partition_root,
)

REPOSITORY = "taher10/Vol-Dashboard"
TAG = "chain-latest"

# The index file keeps its own name; partitions get a prefix so the release
# page reads as a set rather than as loose dates, and so a future asset on
# this tag cannot be mistaken for a month.
INDEX_ASSET = "schwab_database.db"
PARTITION_PREFIX = "chains-"

_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/tags/{TAG}"
_PARTITION_ASSET = re.compile(rf"^{re.escape(PARTITION_PREFIX)}(\d{{4}}-\d{{2}})\.db$")

# A floor against pointers and error pages, not a judgement about the data.
# This used to be 100MB, which made sense when there was one 658MB asset and
# makes none now: the index file is ~1MB and a young month's partition can be
# smaller still. The real check is structural -- it has to open as SQLite and
# carry the table it is supposed to carry -- and no HTML error page does. One
# page is the smallest a SQLite file can be.
_MIN_BYTES = 4096

_CHUNK = 1 << 20
_TIMEOUT_SECONDS = 300

# Generic on purpose: this identifies the program, not the person running it.
_USER_AGENT = "vol-dashboard"


class ChainDownloadError(RuntimeError):
    """An asset could not be fetched, or what arrived was not what it claims."""


def asset_url(name: str) -> str:
    """The stable public download URL for one asset on the fixed tag."""
    return f"https://github.com/{REPOSITORY}/releases/download/{TAG}/{name}"


def partition_asset(month: str) -> str:
    """"2026-11" -> "chains-2026-11.db"."""
    return f"{PARTITION_PREFIX}{month}.db"


def _request(url: str, *, method: str = "GET", accept: str | None = None) -> urllib.request.Request:
    headers = {"User-Agent": _USER_AGENT}
    if accept:
        headers["Accept"] = accept
    # Authenticated when a token happens to be in the environment, which in
    # CI it is. Not required: every asset here is public. It exists because
    # the unauthenticated GitHub API allows 60 calls an hour per IP and
    # Actions runners share addresses, so an unlucky run would fail on a
    # rate limit rather than on anything real.
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, method=method, headers=headers)


def published_assets(*, timeout: int = 30) -> dict[str, int]:
    """{asset name -> byte size} for everything on the release, or {}.

    Asked rather than assumed, so adding a month upstream needs no change
    here and a month that was never published cannot be requested. An empty
    result means "could not find out", which callers treat as "do not
    download" rather than "nothing exists" -- being wrong the other way would
    replace a good local store with nothing.
    """
    try:
        with urllib.request.urlopen(
            _request(_RELEASE_API, accept="application/vnd.github+json"), timeout=timeout
        ) as response:
            payload = json.load(response)
    except (urllib.error.URLError, OSError, ValueError):
        return {}
    assets = payload.get("assets") if isinstance(payload, dict) else None
    if not isinstance(assets, list):
        return {}
    return {
        asset["name"]: int(asset.get("size", 0))
        for asset in assets
        if isinstance(asset, dict) and isinstance(asset.get("name"), str)
    }


def published_months(assets: dict[str, int] | None = None) -> list[str]:
    """Months published on the release, newest last."""
    names = assets if assets is not None else published_assets()
    return sorted(
        match.group(1) for match in (_PARTITION_ASSET.match(n) for n in names) if match
    )


def table_count(path: Path | str, table: str) -> int:
    """Rows in `table`, or raise if the file is not a database that has it.

    Read-only URI so this can never create an empty file at a mistyped
    path -- sqlite3.connect() on a missing path would otherwise produce a
    valid, empty database and make "is it there?" answer yes.
    """
    connection = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    try:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        connection.close()


def verify(path: Path | str, table: str, *, min_rows: int = 1) -> int:
    """Check a file is a database carrying `table`. Returns its row count."""
    path = Path(path)
    if not path.exists():
        raise ChainDownloadError(f"{path} does not exist")
    size = path.stat().st_size
    if size < _MIN_BYTES:
        raise ChainDownloadError(
            f"{path} is {size:,} bytes, below the {_MIN_BYTES:,} floor -- "
            "smaller than any SQLite file, so this is a pointer, an error "
            "page or a truncated transfer rather than data."
        )
    try:
        rows = table_count(path, table)
    except sqlite3.DatabaseError as exc:
        raise ChainDownloadError(
            f"{path} is not a readable SQLite database with a {table} table ({exc})"
        ) from exc
    if rows < min_rows:
        raise ChainDownloadError(
            f"{path} holds {rows:,} rows in {table}, fewer than the required {min_rows:,}"
        )
    return rows


def _existing_count(path: Path, table: str) -> int | None:
    """Rows already at `path`, or None if it is unusable.

    Unusable covers "absent" and "corrupt" alike, because both mean the same
    thing here: there is nothing worth protecting, so a download may proceed.
    """
    if not path.exists():
        return None
    try:
        return table_count(path, table)
    except sqlite3.DatabaseError:
        return None


def download_asset(
    name: str,
    dest: Path | str,
    table: str,
    *,
    url: str | None = None,
    allow_fewer_rows: bool = False,
    timeout: int = _TIMEOUT_SECONDS,
) -> int:
    """Fetch one asset to `dest`, verifying before it replaces anything.

    Returns the downloaded file's row count in `table`.

    By default this refuses to replace a local file with one holding fewer
    rows. Both the chain partitions and the price table are append-only, so a
    smaller remote copy means something went wrong upstream rather than that
    history was revised -- and the local file may be the only copy of a
    session. Pass allow_fewer_rows=True for a deliberate rollback.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Beside the destination, not in /tmp: os.replace() is only atomic within
    # a single filesystem, and on this project /tmp routinely is not one.
    temporary = dest.with_name(dest.name + ".download")

    try:
        try:
            with urllib.request.urlopen(
                _request(url or asset_url(name)), timeout=timeout
            ) as response, open(temporary, "wb") as handle:
                while chunk := response.read(_CHUNK):
                    handle.write(chunk)
        except urllib.error.HTTPError as exc:
            hint = (
                f" -- no asset named {name} on the {TAG} release"
                if exc.code == 404
                else ""
            )
            raise ChainDownloadError(
                f"{url or asset_url(name)} returned HTTP {exc.code}{hint}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ChainDownloadError(
                f"Could not reach {url or asset_url(name)} ({exc.reason})"
            ) from exc

        downloaded = verify(temporary, table)

        existing = _existing_count(dest, table)
        if existing is not None and downloaded < existing and not allow_fewer_rows:
            raise ChainDownloadError(
                f"Refusing to replace {dest}: it holds {existing:,} rows in {table} and "
                f"the download holds {downloaded:,}. These tables only ever grow, so a "
                "smaller asset means something went wrong upstream."
            )

        os.replace(temporary, dest)
        return downloaded
    finally:
        # Never leave a partial file behind to be mistaken for data, or to
        # occupy space after a failure nobody noticed.
        temporary.unlink(missing_ok=True)


def sync(
    db_path: Path | str | None = None,
    *,
    months: list[str] | None = None,
    newest: int | None = None,
    allow_fewer_rows: bool = False,
    timeout: int = _TIMEOUT_SECONDS,
) -> dict:
    """Bring the local store up to date from the release. Returns a summary.

    `months` names partitions explicitly; `newest` takes that many of the
    most recent published ones; neither means all of them.

    A nightly run wants `newest=2` -- it only ever appends to the current
    month, and two covers the week where a session's date and the calendar
    month disagree. A developer wants the default, because the dashboard can
    be asked for any date in the archive.

    The index file comes across every time. It is ~1MB, and it is what
    carries the prices, the precomputed metrics, and the record of which
    dates exist in months this machine did not download. That last part is
    why the index is NOT rebuilt here: rebuild_index() derives the index from
    the partitions present, which after a partial sync is a subset, and
    publishing that back would erase four months of history from the only
    record of it. Rebuilding is for a store that holds everything -- a demo
    build, a repair -- and SchwabDatabase heals an index that is empty
    rather than one that is merely partial.
    """
    assets = published_assets(timeout=min(timeout, 30))
    if not assets:
        raise ChainDownloadError(
            f"Could not list the assets on {TAG}. Refusing to guess what to "
            "download: an empty answer here means the release could not be "
            "read, not that it is empty."
        )
    if INDEX_ASSET not in assets:
        raise ChainDownloadError(
            f"The {TAG} release has no {INDEX_ASSET}. Without it there is no "
            "record of which dates exist, so a sync would silently narrow the "
            f"archive to whatever partitions arrived. Published: {sorted(assets)}"
        )

    resolved = resolve_db_path(db_path)
    wanted = published_months(assets)
    if months is not None:
        missing = [m for m in months if partition_asset(m) not in assets]
        if missing:
            raise ChainDownloadError(
                f"Not published on {TAG}: {', '.join(partition_asset(m) for m in missing)}"
            )
        wanted = [m for m in wanted if m in months]
    elif newest is not None:
        wanted = wanted[-newest:] if newest > 0 else []

    summary: dict = {"index": None, "downloaded": [], "skipped": [], "months": wanted}

    summary["index"] = download_asset(
        INDEX_ASSET, resolved, "stock_prices",
        allow_fewer_rows=allow_fewer_rows, timeout=timeout,
    )

    if _is_pre_migration(resolved):
        raise ChainDownloadError(
            f"The published {INDEX_ASSET} is the pre-migration single-table "
            "database -- it still carries an `options` table and there are no "
            "chain partitions beside it. Convert it with `python -m "
            "src.migrate_to_partitions` before anything relies on this; the "
            "one-off workflow that did it in CI has served its purpose and "
            "been deleted. Refusing to continue: a run that collected into this "
            "would publish an index describing only today over a release that "
            "describes four months."
        )

    root = resolve_partition_root(resolved)
    for month in wanted:
        name = partition_asset(month)
        local = root / f"{month}.db"
        if local.exists() and local.stat().st_size == assets[name]:
            # Byte-for-byte size equality on an append-only file: nothing was
            # added upstream. Being wrong about "unchanged" costs one
            # transfer; being wrong the other way is silent and lasting, so
            # anything other than an exact match downloads.
            summary["skipped"].append(month)
            continue
        rows = download_asset(
            name, local, "chains",
            allow_fewer_rows=allow_fewer_rows, timeout=timeout,
        )
        summary["downloaded"].append((month, rows))

    return summary


def _is_pre_migration(path: Path) -> bool:
    """True when `path` is the old one-row-per-contract database.

    The two eras share a filename, which is deliberate -- the local path and
    the asset name did not need to churn -- so the way to tell them apart is
    what is inside. The pre-migration file carries an `options` table and no
    chain partitions; the migrated one carries `chain_index` and has
    partitions beside it.

    Checked after the download rather than before, because the published
    asset is the only thing that can answer it and it has already passed
    every other test: it is valid SQLite and it has the prices. Nothing is
    damaged by having fetched it; the refusal is about what would happen
    next.
    """
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    except sqlite3.DatabaseError:
        return False
    finally:
        connection.close()
    if "options" not in tables:
        return False
    root = resolve_partition_root(path)
    return not (root.is_dir() and any(root.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].db")))


def publishable(db_path: Path | str | None = None) -> list[Path]:
    """Local files to upload, index first.

    Every partition present rather than only the ones this run touched. A
    nightly run holds two of them, so "all of them" is ~30MB and needs no
    diffing logic to be correct -- and `gh release upload --clobber` on an
    unchanged file is a no-op worth paying for over a wrong guess about
    which month a session landed in.
    """
    resolved = resolve_db_path(db_path)
    files = [resolved] if resolved.exists() else []
    root = resolve_partition_root(resolved)
    if root.is_dir():
        files.extend(sorted(root.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].db")))
    return files


def asset_name_for(path: Path) -> str:
    """The asset name a local store file publishes under.

    Partitions are named for their month, so the month is the whole test;
    anything else in the store is the index file.
    """
    if re.fullmatch(r"\d{4}-\d{2}", path.stem):
        return partition_asset(path.stem)
    return INDEX_ASSET


def baseline(db_path: Path | str | None = None) -> dict:
    """Counts to compare against once a run has collected.

    Taken immediately after a sync, so it describes the published state this
    run started from. The publish step refuses anything smaller -- and
    refuses outright if this was never taken, because without it a database
    holding only today's session is indistinguishable from one holding four
    months.
    """
    resolved = resolve_db_path(db_path)
    db = SchwabDatabase(db_path=resolved)
    with db._connect() as conn:
        indexed = int(conn.execute("SELECT COUNT(*) FROM chain_index").fetchone()[0])
        prices = int(conn.execute("SELECT COUNT(*) FROM stock_prices").fetchone()[0])
    return {
        "indexed": indexed,
        "prices": prices,
        "partitions": {
            path.stem: table_count(path, "chains") for path in db.partition_paths()
        },
    }


def check_local(
    db_path: Path | str | None = None,
    *,
    min_chains: int = 1,
    against: dict | None = None,
) -> dict:
    """Confirm the local store is consistent, and say whether it grew.

    The failure this guards is the one that destroys history: a run that
    started from an empty or partial store, collected one session into it,
    and published that over four months of chains. So the index has to
    describe at least as much as the partitions do, every partition has to
    open and carry chains, and the whole thing has to hold something.

    With `against` -- a dict from baseline() -- it also refuses any count
    that has gone DOWN since the run started. These tables only ever grow,
    so a smaller number is not a revision, it is damage, and the one thing
    that must never be uploaded over the published copy.

    The returned dict carries `grew`, which distinguishes "collected
    something" from "collected nothing". Nothing collected is a normal
    outcome -- a run outside market hours raises LiveDataUnavailableError
    and writes nothing -- and the right response is to leave the release
    alone rather than to re-upload identical bytes or to fail.
    """
    resolved = resolve_db_path(db_path)
    if not resolved.exists():
        raise ChainDownloadError(f"No store at {resolved}")

    verify(resolved, "stock_prices", min_rows=0)
    db = SchwabDatabase(db_path=resolved)

    partitions = db.partition_paths()
    counts = {path.stem: verify(path, "chains") for path in partitions}
    in_partitions = sum(counts.values())

    with db._connect() as conn:
        indexed = int(conn.execute("SELECT COUNT(*) FROM chain_index").fetchone()[0])
        prices = int(conn.execute("SELECT COUNT(*) FROM stock_prices").fetchone()[0])

    if in_partitions < min_chains:
        raise ChainDownloadError(
            f"{resolved} holds {in_partitions} chains across {len(partitions)} "
            f"partition(s), fewer than the required {min_chains}"
        )
    if indexed < in_partitions:
        raise ChainDownloadError(
            f"The index describes {indexed} chains but the partitions hold "
            f"{in_partitions}. The index is the record of months this machine "
            "did not download, so publishing it while it is behind would "
            "narrow the archive. Run rebuild_index() only on a complete store."
        )

    grew = False
    if against is not None:
        if indexed < against["indexed"]:
            raise ChainDownloadError(
                f"The index held {against['indexed']:,} chains when this run started "
                f"and holds {indexed:,} now. It only ever grows, so publishing this "
                "would erase sessions from the only record of them."
            )
        if prices < against["prices"]:
            raise ChainDownloadError(
                f"stock_prices held {against['prices']:,} rows when this run started "
                f"and holds {prices:,} now."
            )
        for month, before in against["partitions"].items():
            if month not in counts:
                raise ChainDownloadError(
                    f"The {month} partition held {before:,} chains when this run "
                    "started and is now missing. Publishing without it would not "
                    "remove the asset, but the run cannot be trusted."
                )
            if counts[month] < before:
                raise ChainDownloadError(
                    f"The {month} partition held {before:,} chains when this run "
                    f"started and holds {counts[month]:,} now."
                )
        grew = (
            indexed > against["indexed"]
            or prices > against["prices"]
            or counts != against["partitions"]
        )

    return {
        "partitions": len(partitions),
        "chains_in_partitions": in_partitions,
        "chains_indexed": indexed,
        "prices": prices,
        "counts": counts,
        "grew": grew,
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Move the chain store to and from its release assets.")
    sub = parser.add_subparsers(dest="command", required=True)

    get = sub.add_parser("sync", help="fetch the index and partitions, verifying before replacing")
    get.add_argument("--db-path", default=None)
    get.add_argument("--month", action="append", dest="months", default=None,
                     help="a specific YYYY-MM partition; repeatable")
    get.add_argument("--newest", type=int, default=None,
                     help="only the N most recent published partitions (a nightly run wants 2)")
    get.add_argument("--allow-fewer-rows", action="store_true")

    mark = sub.add_parser("baseline", help="record the counts this run started from")
    mark.add_argument("--db-path", default=None)
    mark.add_argument("--out", required=True, help="where to write the JSON")

    check = sub.add_parser(
        "check",
        help="confirm the local store is consistent enough to publish. "
             "Exit 0 = publish, 3 = consistent but nothing collected, 1 = refuse.",
    )
    check.add_argument("--db-path", default=None)
    check.add_argument("--min-chains", type=int, default=1)
    check.add_argument("--baseline", default=None,
                       help="JSON from the baseline subcommand; enables the "
                            "'nothing may shrink' check and the exit-3 signal")

    listing = sub.add_parser("assets", help="list what is published on the release")

    pub = sub.add_parser("publishable", help="print local-path<TAB>asset-name for each file to upload")
    pub.add_argument("--db-path", default=None)

    args = parser.parse_args(argv)
    try:
        if args.command == "sync":
            result = sync(args.db_path, months=args.months, newest=args.newest,
                          allow_fewer_rows=args.allow_fewer_rows)
            print(f"index: {result['index']:,} price rows")
            for month, rows in result["downloaded"]:
                print(f"  downloaded {month}: {rows:,} chains")
            for month in result["skipped"]:
                print(f"  up to date {month}")
            if not result["months"]:
                print("  no partitions requested")
        elif args.command == "baseline":
            marks = baseline(args.db_path)
            Path(args.out).write_text(json.dumps(marks, indent=2, sort_keys=True))
            print(
                f"baseline: {marks['indexed']:,} chains indexed, "
                f"{marks['prices']:,} price rows, "
                f"partitions {marks['partitions']}"
            )
        elif args.command == "check":
            against = json.loads(Path(args.baseline).read_text()) if args.baseline else None
            result = check_local(args.db_path, min_chains=args.min_chains, against=against)
            print(
                f"{result['chains_in_partitions']:,} chains in "
                f"{result['partitions']} partition(s), {result['chains_indexed']:,} indexed, "
                f"{result['prices']:,} price rows"
            )
            if against is not None and not result["grew"]:
                print("nothing was collected this run")
                return 3
        elif args.command == "assets":
            assets = published_assets()
            if not assets:
                print(f"could not read the {TAG} release", file=sys.stderr)
                return 1
            for name in sorted(assets):
                print(f"{name}\t{assets[name]:,} bytes")
        else:
            for path in publishable(args.db_path):
                print(f"{path}\t{asset_name_for(path)}")
    except ChainDownloadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
