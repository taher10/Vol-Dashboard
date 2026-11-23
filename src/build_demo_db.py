"""
src/build_demo_db.py

Writes the trimmed databases a demo deployment ships, leaving the live ones
untouched.

Why trim at all
---------------
The chain store holds every session ever collected -- 52 MB across five
monthly partitions as of 2026-11-22, growing ~0.9 MB a session. Baking all
of it into a container image is unnecessary: the chains only back the
surface, calendar legs and strategy builder, and those read the most recent
snapshot. Everything else on the dashboard runs off history/vol_history.db,
which is 1MB for the entire history.

So the chains are cut to a recent window while the metric history is copied
whole -- that keeps IV Rank and the trailing z-scores meaningful (they need
the long series) and still drops most of the bulk.

The absolute numbers matter much less than they did. Before 2026-11-22 this
trim took 658 MB down to ~105 MB and was the difference between a deployable
image and an undeployable one; now the untrimmed store would already fit.
It still runs because a demo should ship the days it shows and not four
months of someone's collection history.

Writes to a separate output directory and never opens a source for writing.
A demo build must not be able to damage the real data; the history database
has already been lost once to a careless overwrite.

Usage:
    python -m src.build_demo_db --days 15 --out demo-data/
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import date
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.schwab_database import SchwabDatabase, resolve_partition_root

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

    # Copy then delete, rather than creating a fresh store and re-encoding
    # into it: this keeps the schema and the stored chain bytes identical to
    # production without restating anything here, where it would quietly fall
    # out of date the next time the real schema changes.
    #
    # Both halves of the store have to come across -- the index file and the
    # monthly chain partitions beside it. Copying only the file `CHAIN_DB`
    # names would produce a demo that knows which dates exist and has no
    # chains for any of them.
    source = SchwabDatabase(db_path=CHAIN_DB)
    print(f"Copying {CHAIN_DB.name} ({_mb(CHAIN_DB):.1f} MB) "
          f"and {len(source.partition_paths())} chain partition(s) ...")
    shutil.copy2(CHAIN_DB, chain_out)
    out_partitions = resolve_partition_root(chain_out)
    out_partitions.mkdir(parents=True, exist_ok=True)
    for partition in source.partition_paths():
        shutil.copy2(partition, out_partitions / partition.name)
    copied_mb = _mb(chain_out) + sum(
        _mb(p) for p in resolve_partition_root(chain_out).glob("*.db")
    )

    db = SchwabDatabase(db_path=chain_out)

    # Skip thin days. A local `python -m src.job SPX` leaves a one-symbol
    # snapshot dated later than the last full collection, and because each
    # symbol resolves its own newest date, shipping that gives the demo a
    # leaderboard where SPX is from today and everything else is from last
    # week. Worse, it reads as a broken product rather than as a stale file.
    #
    # limit=0 means "no limit" would be a trap here, so ask for more days
    # than any archive will hold rather than relying on the default of 8.
    coverage = [(d.isoformat(), n) for d, n in db.snapshot_symbol_counts(limit=100_000)]
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
            "Sync the collected database first, or lower --min-symbols."
        )

    keep = [d for d, _ in full[:days]]

    # delete_options_snapshots() drops whole partitions that keep nothing and
    # VACUUMs the ones it only thins, so the trim actually reclaims space --
    # without it the files stay their original size and this achieves nothing.
    dropped = db.delete_options_snapshots(keep=[date.fromisoformat(d) for d in keep])
    print(f"  kept {len(keep)} full days ({keep[-1]} -> {keep[0]}), dropped {dropped:,} chains")

    _precompute_metrics(chain_out, keep[0])

    if HISTORY_DB.exists():
        shutil.copy2(HISTORY_DB, history_out)
        print(f"Copied {HISTORY_DB.name} whole ({_mb(HISTORY_DB):.1f} MB) -- IV Rank needs the long series")

    kept_partitions = sorted(resolve_partition_root(chain_out).glob("*.db"))
    chain_mb = _mb(chain_out) + sum(_mb(p) for p in kept_partitions)

    print()
    print(f"Demo data written to {out_dir}/")
    print(f"  {chain_out.name:<22} {_mb(chain_out):>7.1f} MB  index + prices + metrics")
    for partition in kept_partitions:
        print(f"    chains/{partition.name:<14} {_mb(partition):>7.1f} MB")
    print(f"  {'chain store total':<22} {chain_mb:>7.1f} MB  (was {copied_mb:.1f} MB)")
    if history_out.exists():
        print(f"  {history_out.name:<22} {_mb(history_out):>7.1f} MB")
    print()
    print("Serve it with:")
    print(f"  DEMO_MODE=1 SCHWAB_DB_PATH={chain_out} uvicorn src.api.main:app")


def _precompute_metrics(chain_db: Path, newest_day: str) -> None:
    """Compute each symbol's metrics once, at build time, and store them.

    The demo runs on a free-tier CPU roughly 30x slower than a laptop:
    compute_all() costs ~150ms per symbol locally and ~5s there, so a
    24-symbol leaderboard took 237 SECONDS on its first request, and the
    instance sleeps after 15 minutes so that cost recurs for every visitor
    after a quiet spell. Nobody waits that long.

    Doing it here is safe in a way it would not be for the live dashboard:
    this snapshot is frozen, so stored metrics can never drift out of date
    with the chain beside them. Only the newest day is precomputed, because
    that is the one every page reads.
    """
    import os

    os.environ["SCHWAB_DB_PATH"] = str(chain_db)
    from src.dashboard.data_loader import adapt_db_chain
    from src.metrics import VolatilityMetrics
    from src.schwab_database import SchwabDatabase
    from src.symbols import SYMBOL_REGISTRY

    db = SchwabDatabase(db_path=chain_db)
    day = date.fromisoformat(newest_day)
    print(f"Precomputing metrics for {newest_day} (makes the demo fast) ...")

    done = skipped = 0
    for symbol in SYMBOL_REGISTRY:
        chain = adapt_db_chain(db.options_snapshot(symbol, day))
        if chain is None or chain.empty:
            skipped += 1
            continue
        prices = db.price_series(symbol)
        metrics = VolatilityMetrics(
            chain, price_history=prices if not prices.empty else None
        ).compute_all()
        if db.write_precomputed_metrics(symbol, day, metrics):
            done += 1
        else:
            skipped += 1
    print(f"  stored metrics for {done} symbols" + (f", skipped {skipped}" if skipped else ""))


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
