"""
src/migrate_to_partitions.py

One-off migration: the single 658 MB `options` table -> compressed monthly
chain partitions. Run once, verify, then never again.

    python -m src.migrate_to_partitions --source database/schwab_database.db

Why this is a script and not an automatic upgrade on first open
--------------------------------------------------------------
The source file is the only copy of four months of option chains. Schwab
serves current chains only, so a day lost here is lost permanently -- the
same reason the 2026-08-28 outage cost two weeks that cannot be recovered.
A migration that ran implicitly would run on a developer's laptop, in CI, and
inside a demo build, at times nobody chose, with no opportunity to compare
the result against the input before the input is gone.

So: this writes the partitions beside the source and *touches nothing else*.
It does not delete, truncate, VACUUM or rename the source. `--verify` reads
every migrated chain back and compares it, row for row and dtype for dtype,
against the query it replaced. Removing the source is a separate decision for
a human to make after that passes.

What "exact" means here
-----------------------
Every caller of options_snapshot() has always received whatever
`SELECT * FROM options WHERE symbol = ? AND snapshot_date = ? ORDER BY
expiration, strike_price` returned. That frame, unchanged, is what gets
encoded -- so the compatibility contract is not "the same numbers", it is
"the same object", down to column order, dtypes, nan/inf, and -0.0.
pandas.testing.assert_frame_equal with check_exact=True and check_dtype=True
is what proves it, and it is run over every slice rather than a sample.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

from src import chain_codec
from src.schwab_database import (
    SchwabDatabase,
    _SLICE_ORDER,
    resolve_partition_root,
)

_LEGACY_SLICE_SQL = (
    f"SELECT * FROM options WHERE symbol = ? AND snapshot_date = ? {_SLICE_ORDER}"
)


def _read_only(path: Path) -> sqlite3.Connection:
    """Open the source so the migration cannot write to it even by mistake.

    Also the reason this is a URI connection: sqlite3.connect() on a path
    that does not exist creates an empty database, which would make a typo
    in --source look like an archive with nothing in it.
    """
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def legacy_slices(source: Path) -> list[tuple[str, str]]:
    """Every (symbol, snapshot_date) the old table holds, oldest first."""
    with _read_only(source) as conn:
        return [
            (r[0], r[1])
            for r in conn.execute(
                "SELECT DISTINCT symbol, snapshot_date FROM options "
                "ORDER BY snapshot_date, symbol"
            )
        ]


def legacy_slice(source: Path, symbol: str, snapshot_date: str) -> pd.DataFrame:
    """The frame the pre-migration read path returned for one chain."""
    with _read_only(source) as conn:
        return pd.read_sql_query(_LEGACY_SLICE_SQL, conn, params=(symbol, snapshot_date))


def copy_prices_and_metrics(source: Path, destination: SchwabDatabase) -> tuple[int, int]:
    """Move stock_prices and precomputed_metrics across verbatim.

    These are small (0.5 MB of prices; metrics only exist in a demo build)
    and their schemas are unchanged, so they transfer as rows rather than
    through any encoding. Copied with INSERT OR REPLACE so re-running the
    migration is not destructive.
    """
    with _read_only(source) as src:
        prices = src.execute(
            "SELECT symbol, price_date, open, high, low, close, volume FROM stock_prices"
        ).fetchall()
        try:
            metrics = src.execute(
                "SELECT symbol, snapshot_date, metric_name, payload FROM precomputed_metrics"
            ).fetchall()
        except sqlite3.OperationalError:
            metrics = []  # table predates that feature

    with destination._connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO stock_prices "
            "(symbol, price_date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
            prices,
        )
        conn.executemany(
            "INSERT OR REPLACE INTO precomputed_metrics "
            "(symbol, snapshot_date, metric_name, payload) VALUES (?,?,?,?)",
            metrics,
        )
    return len(prices), len(metrics)


def migrate(source: Path, destination: Path, progress_every: int = 100) -> dict:
    """Encode every chain in `source` into partitions beside `destination`.

    The encoded frame is the legacy query's own result, not a reconstruction
    of it -- see the module docstring.
    """
    db = SchwabDatabase(db_path=destination)
    slices = legacy_slices(source)
    total_rows = 0
    started = time.monotonic()

    for n, (symbol, snapshot_date) in enumerate(slices, start=1):
        frame = legacy_slice(source, symbol, snapshot_date)
        payload = chain_codec.encode(frame)
        day = date.fromisoformat(snapshot_date)
        with db._partition_write(db.partition_path(day)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO chains (symbol, snapshot_date, contracts, payload) "
                "VALUES (?, ?, ?, ?)",
                (symbol, snapshot_date, len(frame), payload),
            )
        total_rows += len(frame)
        if progress_every and n % progress_every == 0:
            print(f"  {n}/{len(slices)} chains, {total_rows:,} rows", flush=True)

    indexed = db.rebuild_index()
    prices, metrics = copy_prices_and_metrics(source, db)
    return {
        "chains": len(slices),
        "rows": total_rows,
        "indexed": indexed,
        "prices": prices,
        "metrics": metrics,
        "seconds": time.monotonic() - started,
        "partitions": len(db.partition_paths()),
    }


def verify(source: Path, destination: Path, progress_every: int = 100) -> dict:
    """Compare every migrated chain against the query it replaced.

    Returns counts and a list of mismatches. A mismatch is a failed
    migration, not a warning: the source must not be deleted while this
    reports even one.
    """
    db = SchwabDatabase(db_path=destination)
    slices = legacy_slices(source)
    mismatches: list[str] = []
    rows = 0

    for n, (symbol, snapshot_date) in enumerate(slices, start=1):
        expected = legacy_slice(source, symbol, snapshot_date)
        actual = db.options_snapshot(symbol, date.fromisoformat(snapshot_date))
        try:
            pd.testing.assert_frame_equal(
                expected, actual, check_exact=True, check_dtype=True
            )
        except AssertionError as exc:
            first_line = str(exc).strip().splitlines()[0]
            mismatches.append(f"{symbol} {snapshot_date}: {first_line}")
        rows += len(expected)
        if progress_every and n % progress_every == 0:
            print(f"  verified {n}/{len(slices)} chains, {rows:,} rows", flush=True)

    # The index is what the dashboard reads to decide a date exists, so a
    # chain that round-trips but is invisible is still a regression.
    index_pairs = set()
    for symbol in {s for s, _ in slices}:
        for day in db.options_snapshot_dates(symbol):
            index_pairs.add((symbol, day.isoformat()))
    missing_from_index = sorted(set(slices) - index_pairs)

    with _read_only(source) as conn:
        source_rows = conn.execute("SELECT COUNT(*) FROM options").fetchone()[0]

    return {
        "chains": len(slices),
        "rows": rows,
        "source_rows": source_rows,
        "mismatches": mismatches,
        "missing_from_index": missing_from_index,
    }


def _sizes(source: Path, destination: Path) -> str:
    root = resolve_partition_root(destination)
    partitions = sorted(root.glob("*.db")) if root.is_dir() else []
    new_bytes = destination.stat().st_size + sum(p.stat().st_size for p in partitions)
    old_bytes = source.stat().st_size
    lines = [
        f"  before: {old_bytes / 1e6:>8.1f} MB  {source}",
        f"  after:  {new_bytes / 1e6:>8.1f} MB  "
        f"({destination.stat().st_size / 1e6:.1f} MB index+prices "
        f"+ {len(partitions)} partitions)",
        f"  ratio:  {old_bytes / max(new_bytes, 1):>8.1f}x smaller",
    ]
    for p in partitions:
        lines.append(f"      {p.name}  {p.stat().st_size / 1e6:>6.1f} MB")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[2])
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("database/schwab_database.db"),
        help="the pre-migration single-table database (read-only; never modified)",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=None,
        help="where the new index file goes; partitions land beside it. "
             "Defaults to a sibling of --source so the original is left intact.",
    )
    parser.add_argument("--verify-only", action="store_true",
                        help="skip migration, just compare an existing result")
    parser.add_argument("--skip-verify", action="store_true",
                        help="migrate without comparing (not recommended)")
    args = parser.parse_args(argv)

    source = args.source
    destination = args.destination or source.with_name(f"{source.stem}.partitioned.db")
    if not source.exists():
        print(f"No such source: {source}", file=sys.stderr)
        return 2

    if not args.verify_only:
        print(f"Migrating {source} -> {destination}")
        stats = migrate(source, destination)
        print(
            f"\nEncoded {stats['chains']:,} chains ({stats['rows']:,} rows) into "
            f"{stats['partitions']} partitions in {stats['seconds']:.0f}s\n"
            f"  index rows: {stats['indexed']:,}\n"
            f"  prices:     {stats['prices']:,}\n"
            f"  metrics:    {stats['metrics']:,}"
        )
        print(_sizes(source, destination))

    if args.skip_verify:
        print("\nSkipped verification -- do not delete the source.")
        return 0

    print("\nVerifying every chain against the query it replaces...")
    result = verify(source, destination)
    print(
        f"\n  {result['chains']:,} chains, {result['rows']:,} rows compared "
        f"(source holds {result['source_rows']:,})"
    )
    if result["rows"] != result["source_rows"]:
        print(f"  ROW COUNT MISMATCH: {result['rows']:,} != {result['source_rows']:,}")
    for bad in result["mismatches"][:20]:
        print(f"  MISMATCH {bad}")
    for bad in result["missing_from_index"][:20]:
        print(f"  NOT IN INDEX {bad[0]} {bad[1]}")

    ok = (
        not result["mismatches"]
        and not result["missing_from_index"]
        and result["rows"] == result["source_rows"]
    )
    print(
        "\nEvery chain round-tripped exactly. The source can be retired."
        if ok else
        "\nVERIFICATION FAILED -- keep the source."
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
