"""
src/build_synthetic_db.py

Write a simulated market into the database shape the dashboard reads.

This is the public demo's data source. It produces the same tables the
Schwab collector produces -- options, stock_prices, precomputed_metrics --
so every endpoint, cache and page works against it unchanged. Nothing
downstream knows or needs to know the quotes were generated.

Run it, point DEMO_DB_URL at the result, and the public deployment stops
carrying anyone's market data. See src/synthetic_market.py for the model
and for how closely it matches the real thing.

    python -m src.build_synthetic_db --days 15
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from src import synthetic_market as sm
from src.history_store import HistoryStore
from src.schwab_database import _OPTIONS_CHAIN_COLUMNS, SchwabDatabase

_PROJECT_ROOT = Path(__file__).parent.parent

_OPTION_COLUMNS = (
    "symbol snapshot_date contract_symbol option_type expiration dte strike_price "
    "bid ask mark last volume open_interest implied_volatility delta gamma theta "
    "vega rho in_the_money theoretical_value underlying_price fetch_time"
).split()

# synthetic_market.generate_chain() emits storage-shaped rows (snake_case,
# the column names the old `options` table used). append_options_snapshot()
# takes a chain the way Schwab hands one over (camelCase, no underlying
# symbol or snapshot date -- those are arguments). _CHAIN_ALIASES is the one
# mapping between the two, derived from the store's own so the two cannot
# drift: if a column is renamed there, this follows.
_CHAIN_ALIASES = {dest: src for src, dest in _OPTIONS_CHAIN_COLUMNS.items()}


def _as_chain_frame(rows: list[dict]) -> pd.DataFrame:
    """Storage-shaped synthetic rows -> a chain shaped like Schwab's."""
    frame = pd.DataFrame(rows)
    return frame[[c for c in _CHAIN_ALIASES if c in frame.columns]].rename(
        columns=_CHAIN_ALIASES
    )


def _as_price_frame(symbol: str, path: list[tuple]) -> pd.DataFrame:
    """synthetic_market.price_path() rows -> what append_price_history takes.

    Volume is derived from a hash of (symbol, date) rather than randomised,
    so a rebuild of the same market produces the same file. The demo is
    republished weekly and a diff that is all noise tells you nothing about
    whether the generator changed.
    """
    return pd.DataFrame({
        "datetime": pd.to_datetime([d for d, *_ in path]),
        "open": [o for _, o, _, _, _ in path],
        "high": [h for _, _, h, _, _ in path],
        "low": [low for _, _, _, low, _ in path],
        "close": [c for *_, c in path],
        "volume": [
            int(2_000_000 + abs(hash((symbol, d))) % 8_000_000) for d, *_ in path
        ],
    })


def _sessions(end: date, count: int) -> list[date]:
    """The last `count` weekdays, oldest first."""
    out: list[date] = []
    day = end
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day -= timedelta(days=1)
    return sorted(out)


def build(days: int, out_dir: Path, end: date | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = out_dir / "schwab_database.db"
    if db_path.exists():
        db_path.unlink()

    # Writing through SchwabDatabase rather than hand-rolling SQL keeps the
    # synthetic chains definitionally identical in shape to real ones -- same
    # normalisation, same encoding, same stored dtypes. A demo database that
    # had drifted a column would fail in the one place nobody tests.
    db = SchwabDatabase(db_path)

    # The metric history is a second database and is not optional. IV rank,
    # IV percentile and the richness columns all read it, and the committed
    # one holds real history derived from Schwab data -- so a demo running a
    # simulated chain against it would still be publishing real numbers,
    # just less visibly.
    history_path = out_dir / "vol_history.db"
    if history_path.exists():
        history_path.unlink()
    history = HistoryStore(history_path)

    sessions = _sessions(end or date.today(), days)

    total = 0
    history_rows = 0
    for spec in sm.UNIVERSE:
        path = sm.price_path(spec, days=400, end=sessions[-1])
        db.append_price_history(spec.symbol, _as_price_frame(spec.symbol, path))

        # A year of history behind the chains: IV rank is a percentile
        # against the symbol's own past, so a handful of sessions would
        # make it meaningless however good the chains were.
        history_rows += _write_history(history_path, sm.generate_history(spec, path))

        # Spot on each session comes from that same simulated path, so the
        # chain and the price history agree. If they disagreed, every
        # realised-vol and VRP number on the dashboard would be nonsense in
        # a way that is tedious to trace back to its source.
        closes = {d: c for d, _, _, _, c in path}
        for session in sessions:
            spot = closes.get(session)
            if spot is None:
                continue
            rows = sm.generate_chain(spec, session, spot)
            total += db.append_options_snapshot(spec.symbol, session, _as_chain_frame(rows))

    _precompute(db_path, sessions[-1].isoformat())
    print(f"{total:,} contracts across {len(sm.UNIVERSE)} symbols and {len(sessions)} sessions")
    print(f"{history_rows:,} metric-history rows")
    print(f"{db_path}  ({db_path.stat().st_size / 1e6:.1f} MB)")
    print(f"{history_path}  ({history_path.stat().st_size / 1e6:.1f} MB)")
    return db_path


_HISTORY_COLUMNS = "symbol snapshot_date expiration dte atm_iv skew curvature realized_vol vrp".split()


def _write_history(path: Path, rows: list[dict]) -> int:
    if not rows:
        return 0
    conn = sqlite3.connect(path)
    conn.executemany(
        f"INSERT OR REPLACE INTO metric_history ({','.join(_HISTORY_COLUMNS)})"
        f" VALUES ({','.join('?' * len(_HISTORY_COLUMNS))})",
        [tuple(r[c] for c in _HISTORY_COLUMNS) for r in rows],
    )
    conn.commit()
    conn.close()
    return len(rows)


def _precompute(db_path: Path, newest_day: str) -> None:
    """Fill precomputed_metrics for the newest session.

    Reuses the demo builder's own routine rather than reimplementing it:
    the demo is unusably slow without these frames, and two copies of the
    logic would eventually warm different things.
    """
    from src.build_demo_db import _precompute_metrics

    _precompute_metrics(db_path, newest_day)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a simulated market for the public demo.")
    parser.add_argument("--days", type=int, default=15, help="Trading sessions to generate (default 15).")
    parser.add_argument("--out", type=Path, default=_PROJECT_ROOT / "demo-data", help="Output directory.")
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        default=None,
        help="Newest session (YYYY-MM-DD). Defaults to today, so a scheduled rebuild stays current.",
    )
    args = parser.parse_args()

    build(days=args.days, out_dir=args.out, end=args.end)
    return 0


if __name__ == "__main__":
    sys.exit(main())
