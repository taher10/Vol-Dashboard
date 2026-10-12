"""
src/build_demo_db.py

Writes the trimmed databases a demo deployment ships, leaving the live ones
untouched.

Why trim at all
---------------
database/schwab_database.db is ~260MB and grows ~10MB a day. Baking that
into a container image is slow to build, slow to deploy, and unnecessary:
the chain tables only back the surface, calendar legs and strategy builder,
and those read the most recent snapshot. Everything else on the dashboard
runs off history/vol_history.db, which is 1MB for the entire history.

So the chain database is cut to a recent window while the metric history is
copied whole -- that keeps IV Rank and the trailing z-scores meaningful
(they need the long series) and still drops most of the bulk.

Writes to a separate output directory and never opens a source for writing.
A demo build must not be able to damage the real data; the history database
has already been lost once to a careless overwrite.

Usage:
    python -m src.build_demo_db --days 15 --out demo-data/
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

CHAIN_DB = _PROJECT_ROOT / "database" / "schwab_database.db"
HISTORY_DB = _PROJECT_ROOT / "history" / "vol_history.db"


def _mb(path: Path) -> float:
    return path.stat().st_size / 1e6


def build(days: int, out_dir: Path, min_symbols: int = 20) -> None:
    if not CHAIN_DB.exists():
        raise SystemExit(f"No chain database at {CHAIN_DB}. `git pull` first.")

    out_dir.mkdir(parents=True, exist_ok=True)
    chain_out = out_dir / "schwab_database.db"
    history_out = out_dir / "vol_history.db"

    # Copy then delete, rather than creating a fresh file and inserting into
    # it: this keeps the schema, indexes and constraints byte-identical to
    # production without restating them here, where they would quietly fall
    # out of date the next time the real schema changes.
    print(f"Copying {CHAIN_DB.name} ({_mb(CHAIN_DB):.0f} MB) ...")
    shutil.copy2(CHAIN_DB, chain_out)

    conn = sqlite3.connect(chain_out)

    # Skip thin days. A local `python -m src.job SPX` leaves a one-symbol
    # snapshot dated later than the last full collection, and because each
    # symbol resolves its own newest date, shipping that gives the demo a
    # leaderboard where SPX is from today and everything else is from last
    # week. Worse, it reads as a broken product rather than as a stale file.
    coverage = list(conn.execute(
        "SELECT snapshot_date, COUNT(DISTINCT symbol) FROM options "
        "GROUP BY snapshot_date ORDER BY snapshot_date DESC"
    ))
    if not coverage:
        raise SystemExit("No snapshots found in the chain database.")

    full = [(d, n) for d, n in coverage if n >= min_symbols]
    thin = [(d, n) for d, n in coverage[:days] if n < min_symbols]
    if thin:
        print(f"  skipping {len(thin)} partial day(s) below --min-symbols={min_symbols}:")
        for d, n in thin:
            print(f"    {d}  only {n} symbol(s)")
    if not full:
        raise SystemExit(
            f"No snapshot has at least {min_symbols} symbols. "
            "`git pull` to fetch the collected database, or lower --min-symbols."
        )

    keep = [d for d, _ in full[:days]]

    placeholders = ",".join("?" for _ in keep)
    deleted = conn.execute(
        f"DELETE FROM options WHERE snapshot_date NOT IN ({placeholders})", keep
    ).rowcount
    conn.commit()
    print(f"  kept {len(keep)} full days ({keep[-1]} -> {keep[0]}), dropped {deleted:,} contract rows")

    # VACUUM is what actually reclaims the pages. Without it the file stays
    # its original size on disk and the trim achieves nothing.
    print("  vacuuming ...")
    conn.execute("VACUUM")
    conn.close()

    if HISTORY_DB.exists():
        shutil.copy2(HISTORY_DB, history_out)
        print(f"Copied {HISTORY_DB.name} whole ({_mb(HISTORY_DB):.1f} MB) -- IV Rank needs the long series")

    print()
    print(f"Demo data written to {out_dir}/")
    print(f"  {chain_out.name:<22} {_mb(chain_out):>7.1f} MB  (was {_mb(CHAIN_DB):.0f} MB)")
    if history_out.exists():
        print(f"  {history_out.name:<22} {_mb(history_out):>7.1f} MB")
    print()
    print("Serve it with:")
    print(f"  DEMO_MODE=1 SCHWAB_DB_PATH={chain_out} uvicorn src.api.main:app")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build trimmed databases for the demo deployment.")
    parser.add_argument("--days", type=int, default=15, help="Recent snapshot days to keep (default 15).")
    parser.add_argument("--out", type=Path, default=_PROJECT_ROOT / "demo-data", help="Output directory.")
    parser.add_argument(
        "--min-symbols", type=int, default=20,
        help="Skip snapshot days with fewer symbols than this (default 20 of 24) -- keeps\npartial local test runs out of the demo.",
    )
    args = parser.parse_args()
    build(args.days, args.out, args.min_symbols)
    return 0


if __name__ == "__main__":
    sys.exit(main())
