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
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.data_quality import market_session_date

CHAIN_DB = _PROJECT_ROOT / "database" / "schwab_database.db"
HISTORY_DB = _PROJECT_ROOT / "history" / "vol_history.db"


def build_mapping(chain_db: Path) -> dict[str, str]:
    """{stored snapshot_date -> correct session date}, for dates that move.

    Keyed on the latest fetch_time within each snapshot: a run writes its
    symbols over several minutes, and the last of them is still the same
    session as the first.
    """
    with sqlite3.connect(f"file:{chain_db}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT snapshot_date, MAX(fetch_time) FROM options "
            "WHERE fetch_time IS NOT NULL GROUP BY snapshot_date ORDER BY snapshot_date"
        ).fetchall()

    mapping: dict[str, str] = {}
    for stored, fetched in rows:
        session = market_session_date(datetime.fromisoformat(fetched))
        if str(session) != stored:
            mapping[stored] = str(session)
    return mapping


def _conflicts(conn: sqlite3.Connection, source: str, destination: str) -> list[tuple]:
    """Rows that cannot simply move because the destination already holds
    that contract for that symbol."""
    return conn.execute(
        "SELECT s.symbol, s.contract_symbol FROM options s "
        "JOIN options d ON d.symbol = s.symbol "
        "        AND d.contract_symbol = s.contract_symbol "
        "        AND d.snapshot_date = ? "
        "WHERE s.snapshot_date = ?",
        (destination, source),
    ).fetchall()


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
    """
    stats = {"moved": 0, "dropped": 0, "history_moved": 0, "details": []}
    ordered = sorted(mapping)  # oldest first: vacates each destination in turn

    with sqlite3.connect(chain_db) as conn:
        for stored in ordered:
            destination = mapping[stored]
            clash = _conflicts(conn, stored, destination)
            if clash:
                conn.executemany(
                    "DELETE FROM options WHERE snapshot_date = ? AND symbol = ? AND contract_symbol = ?",
                    [(stored, sym, contract) for sym, contract in clash],
                )
                stats["dropped"] += len(clash)
            moved = conn.execute(
                "UPDATE options SET snapshot_date = ? WHERE snapshot_date = ?", (destination, stored)
            ).rowcount
            stats["moved"] += moved
            stats["details"].append((stored, destination, moved, clash))
        conn.commit()

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
    print(f"  {'stored':<12} {'->':<4} {'session':<12} {'rows':>9}  note")
    for stored, destination, moved, clash in stats["details"]:
        note = ""
        if clash:
            symbols = sorted({c[0] for c in clash})
            note = f"merged: {len(clash)} duplicate row(s) dropped ({', '.join(symbols)})"
        print(f"  {stored:<12} {'->':<4} {destination:<12} {moved:>9,}  {note}")
    print()
    print(f"chain database : {stats['moved']:,} rows relabelled, {stats['dropped']:,} duplicates dropped")
    print(f"metric history : {stats['history_moved']:,} rows relabelled")
    if dry_run:
        print("\nDRY RUN -- this ran against throwaway copies; your databases are untouched.")
        print("Re-run with --apply to do it for real.")


def dry_run(chain_db: Path, history_db: Path, workdir: Path) -> dict:
    """Run the real thing against copies, so the preview cannot be wrong."""
    workdir.mkdir(parents=True, exist_ok=True)
    chain_copy = workdir / "chain.db"
    history_copy = workdir / "history.db"
    print(f"copying databases to preview against ({chain_db.stat().st_size / 1e6:.0f} MB) ...")
    shutil.copy2(chain_db, chain_copy)
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
    for path in (args.chain_db, args.history_db):
        if path.exists():
            backup = path.with_suffix(path.suffix + ".pre-backfill")
            shutil.copy2(path, backup)
            print(f"backed up {path.name} -> {backup.name}")

    stats = _relabel(args.chain_db, args.history_db, preview["mapping"])
    print(f"\napplied: {stats['moved']:,} chain rows, {stats['history_moved']:,} history rows")
    print("Re-run without --apply to confirm there is nothing left to do.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
