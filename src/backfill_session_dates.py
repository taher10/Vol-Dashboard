"""
src/backfill_session_dates.py

Relabels historical snapshots onto the market session they actually hold.

Why
---
Snapshots were dated `datetime.now(UTC).date()` until
data_quality.market_session_date() replaced it. The cron fires at 21:30 UTC
-- 17:30 ET, after the close -- but GitHub's queue delays runs two to three
hours, and 21:30 + 3h crosses midnight UTC while it is still the same
evening in New York. Wednesday's close got filed under Thursday.

20 of 35 stored dates are wrong, including a 2026-09-26 labelled to a
Saturday. IV Rank, the trailing z-scores, VRP and the backtest all index on
snapshot_date, so every one of those comparisons is a day out of step with
the market until this is run.

How the correct date is derived
-------------------------------
From `options.fetch_time`, which records when the quotes were actually
pulled, run through the same market_session_date() the live path now uses.
history/vol_history.db has no timestamp column of its own, so it is
relabelled using the mapping derived from the chain database -- the two are
written by the same job for the same run, so a shared mapping keeps them
consistent by construction rather than by coincidence.

Two things that make this safe to run
-------------------------------------
**Dry run by default.** It prints exactly what it would do and changes
nothing. `--apply` is required to write, and takes a backup of both files
first.

**Ascending order.** Relabelling 10-14 -> 10-13 collides with 10-13 unless
10-13 -> 10-12 has already happened. Processing oldest first vacates each
destination before it is needed, which turns almost every apparent
collision into a non-event.

A genuine collision remains possible: a partial local run fetched after
midnight ET belongs to the previous session, which may already hold a full
collection. The later fetch is the staler one -- hours past the close, thin
and wide -- so the existing rows win and the conflicting source rows are
dropped. The dry run names every such row before anything is written.

Usage:
    python -m src.backfill_session_dates              # dry run
    python -m src.backfill_session_dates --apply
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import date as dateobj, datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.data_quality import market_session_date
from src.schwab_database import SchwabDatabase, resolve_partition_root

CHAIN_DB = _PROJECT_ROOT / "database" / "schwab_database.db"
HISTORY_DB = _PROJECT_ROOT / "history" / "vol_history.db"


def build_mapping(chain_db: Path) -> dict[str, str]:
    """{stored snapshot_date -> correct session date}, for dates that move.

    Keyed on the latest fetch_time within each snapshot: a run writes its
    symbols over several minutes, and the last of them is still the same
    session as the first.

    fetch_time lives inside the compressed chains rather than in a column
    that can be aggregated, so this decodes each stored chain to read it.
    That is a few seconds across a whole archive, and this script runs once
    in the life of a mislabelling -- not a reason to denormalise a timestamp
    into the index, where it would have to be kept true forever to answer a
    question nobody asks twice.
    """
    db = SchwabDatabase(db_path=chain_db)
    latest: dict[str, str] = {}
    for symbol, day in _stored_slices(db):
        chain = db.options_snapshot(symbol, day)
        if chain.empty or "fetch_time" not in chain.columns:
            continue
        fetched = chain["fetch_time"].dropna()
        if fetched.empty:
            continue
        stored = day.isoformat()
        newest = max(str(v) for v in fetched)
        if stored not in latest or newest > latest[stored]:
            latest[stored] = newest

    mapping: dict[str, str] = {}
    for stored, fetched in sorted(latest.items()):
        session = market_session_date(datetime.fromisoformat(fetched))
        if str(session) != stored:
            mapping[stored] = str(session)
    return mapping


def _stored_slices(db: SchwabDatabase) -> list[tuple[str, dateobj]]:
    """Every (symbol, date) chain the store holds, oldest first."""
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT symbol, snapshot_date FROM chain_index ORDER BY snapshot_date, symbol"
        ).fetchall()
    return [(symbol, dateobj.fromisoformat(day)) for symbol, day in rows]


def _relabel(chain_db: Path, history_db: Path | None, mapping: dict[str, str]) -> dict:
    """Do the work and report what happened. Caller decides whether to keep it.

    The dry run and the real run are THIS function -- the only difference is
    whether it is pointed at the live files or at throwaway copies. An
    earlier version previewed by querying the current state instead, and
    reported 375,606 rows would be dropped when the true figure is a
    handful: relabelling 09-30 -> 09-29 looks like a total collision until
    you notice 09-29 has already moved to 09-28. A preview that disagrees
    with the apply is worse than no preview, because it teaches you to
    ignore it.

    A collision is now resolved per (symbol, date) rather than per contract,
    because a chain is stored whole. That is the same rule stated at the
    granularity the data actually has: a symbol whose destination is already
    collected keeps the destination's copy, and the misfiled one is dropped
    entire instead of row by row. The dry run still names every symbol it
    drops.
    """
    stats = {"moved": 0, "dropped": 0, "history_moved": 0, "details": []}
    ordered = sorted(mapping)  # oldest first: vacates each destination in turn

    db = SchwabDatabase(db_path=chain_db)
    for stored in ordered:
        destination = mapping[stored]
        source_day = dateobj.fromisoformat(stored)
        dest_day = dateobj.fromisoformat(destination)
        before = {s for s, d in _stored_slices(db) if d == source_day}
        kept = {s for s, d in _stored_slices(db) if d == dest_day}
        clash = [(symbol, "(whole chain)") for symbol in sorted(before & kept)]
        moved = db.relabel_snapshot_date(source_day, dest_day)
        stats["moved"] += moved
        stats["dropped"] += len(clash)
        stats["details"].append((stored, destination, moved, clash))

    if history_db is not None and history_db.exists():
        with sqlite3.connect(history_db) as conn:
            for stored in ordered:
                conn.execute(
                    "DELETE FROM metric_history WHERE snapshot_date = ? AND (symbol, expiration) IN "
                    "(SELECT symbol, expiration FROM metric_history WHERE snapshot_date = ?)",
                    (mapping[stored], stored),
                )
                stats["history_moved"] += conn.execute(
                    "UPDATE metric_history SET snapshot_date = ? WHERE snapshot_date = ?",
                    (mapping[stored], stored),
                ).rowcount
            conn.commit()

    return stats


def _report(mapping: dict[str, str], stats: dict, dry_run: bool) -> None:
    print(f"{len(mapping)} snapshot date(s) relabelled:\n")
    print(f"  {'stored':<12} {'->':<4} {'session':<12} {'chains':>9}  note")
    for stored, destination, moved, clash in stats["details"]:
        note = ""
        if clash:
            symbols = sorted({c[0] for c in clash})
            note = f"merged: {len(clash)} duplicate chain(s) dropped ({', '.join(symbols)})"
        print(f"  {stored:<12} {'->':<4} {destination:<12} {moved:>9,}  {note}")
    print()
    print(f"chain database : {stats['moved']:,} chains relabelled, {stats['dropped']:,} duplicate chain(s) dropped")
    print(f"metric history : {stats['history_moved']:,} rows relabelled")
    if dry_run:
        print("\nDRY RUN -- this ran against throwaway copies; your databases are untouched.")
        print("Re-run with --apply to do it for real.")


def _copy_store(chain_db: Path, destination: Path) -> float:
    """Copy a chain store -- index file and monthly partitions -- returning MB.

    Both halves or neither. Copying only the file named on the command line
    would give the preview an index that lists dates whose chains are not
    there, and a relabelling that appeared to move nothing.
    """
    shutil.copy2(chain_db, destination)
    total = chain_db.stat().st_size
    source_root = resolve_partition_root(chain_db)
    if source_root.is_dir():
        dest_root = resolve_partition_root(destination)
        dest_root.mkdir(parents=True, exist_ok=True)
        for partition in sorted(source_root.glob("*.db")):
            shutil.copy2(partition, dest_root / partition.name)
            total += partition.stat().st_size
    return total / 1e6


def dry_run(chain_db: Path, history_db: Path, workdir: Path) -> dict:
    """Run the real thing against copies, so the preview cannot be wrong."""
    workdir.mkdir(parents=True, exist_ok=True)
    chain_copy = workdir / "chain.db"
    history_copy = workdir / "history.db"
    size = _copy_store(chain_db, chain_copy)
    print(f"copying databases to preview against ({size:.1f} MB) ...")
    if history_db.exists():
        shutil.copy2(history_db, history_copy)

    mapping = build_mapping(chain_copy)
    if not mapping:
        return {}
    stats = _relabel(chain_copy, history_copy if history_db.exists() else None, mapping)

    # The copies have been relabelled; re-deriving must now find nothing.
    leftover = build_mapping(chain_copy)
    if leftover:
        print(f"\nWARNING: {len(leftover)} date(s) would STILL be wrong afterwards: {leftover}")
    else:
        print("verified on the copies: every date matches its session afterwards\n")
    return {"mapping": mapping, "stats": stats}


def main() -> int:
    parser = argparse.ArgumentParser(description="Relabel snapshots onto their true market session.")
    parser.add_argument("--apply", action="store_true", help="Actually write (default is a dry run).")
    parser.add_argument("--chain-db", type=Path, default=CHAIN_DB)
    parser.add_argument("--history-db", type=Path, default=HISTORY_DB)
    args = parser.parse_args()

    if not args.chain_db.exists():
        raise SystemExit(f"No chain database at {args.chain_db}.")

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        preview = dry_run(args.chain_db, args.history_db, Path(tmp))

    if not preview:
        print("Every stored snapshot already matches its market session. Nothing to do.")
        return 0

    _report(preview["mapping"], preview["stats"], dry_run=not args.apply)

    if not args.apply:
        return 0

    print()
    # The chain store's backup has to include its partitions, or the backup
    # is an index pointing at chains that were relabelled under it.
    chain_backup = args.chain_db.with_suffix(args.chain_db.suffix + ".pre-backfill")
    _copy_store(args.chain_db, chain_backup)
    print(f"backed up {args.chain_db.name} (+ partitions) -> {chain_backup.name}")
    if args.history_db.exists():
        history_backup = args.history_db.with_suffix(args.history_db.suffix + ".pre-backfill")
        shutil.copy2(args.history_db, history_backup)
        print(f"backed up {args.history_db.name} -> {history_backup.name}")

    stats = _relabel(args.chain_db, args.history_db, preview["mapping"])
    print(f"\napplied: {stats['moved']:,} chains, {stats['history_moved']:,} history rows")
    print("Re-run without --apply to confirm there is nothing left to do.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
