"""
src/schwab_database.py

SchwabDatabase -- append-only accumulation of raw daily options-chain and
stock-close-price data. One compressed chain per (symbol, snapshot_date), and
one row per symbol-per-day of OHLCV prices.

Why this exists, and how it differs from the other two stores
---------------------------------------------------------------
- CSVStore (data_store.py) persists data/, gitignored, overwrites same-day
  files -- "what does today's snapshot look like," nothing accumulates.
- HistoryStore (history_store.py) accumulates DERIVED per-expiry metrics
  (atm_iv/skew/curvature/realized_vol/vrp) -- compact, one row per
  (symbol, date, expiration), good for IV Rank / trailing z-scores.
- SchwabDatabase (this module) accumulates the RAW data itself -- every
  contract's full quote (bid/ask/greeks/IV/volume/OI) per day, and daily
  OHLCV close prices -- so future features that need more than the
  precomputed metrics (historical smile reconstruction, per-contract
  volume/OI trends, custom metrics not computed at snapshot time, actual
  price charting) have real history to work with instead of only today's
  snapshot.

How the data is laid out, and why it changed
--------------------------------------------
Until 2026-11-22 this was a single SQLite file with one row per contract per
day: 2,042,262 rows, 658 MB, Git-LFS-tracked, growing 12.2 MB per session.
LFS stores a complete new object per version and cannot delta-compress
SQLite, so 58 nightly pushes consumed 9.01 GB of a 10 GB allowance while the
daily growth rate never changed once. That arrangement had a date on which it
would stop working, which is the wrong property for the only copy of data
Schwab does not serve twice.

Three things made it expensive, none of them the data itself:

  - 36% of the file was index. `idx_options_symbol_date` duplicated the
    primary key's own leading columns outright; `idx_options_contract` cost
    80 MB to serve one endpoint the frontend never calls.
  - `symbol`, `snapshot_date` and `option_type` were stored as repeated
    strings on every one of 2 million rows.
  - Every read was the same shape -- `WHERE symbol = ? AND snapshot_date = ?`
    -- so the table paid full relational cost for what is really a key-value
    lookup returning a whole DataFrame.

So a chain is now stored as one compressed blob per (symbol, snapshot_date),
keyed exactly the way it is read. See src/chain_codec.py for the encoding and
the measurements: 658 MB becomes 48.7 MB, 13.5x smaller, and a chain decodes
in 2.8 ms against 6.3 ms for the SELECT it replaces. The payloads live in
monthly partitions:

    database/schwab_database.db           ~1 MB  index + prices + metrics
    database/schwab_database_chains/
        2026-07.db   2026-08.db   ...     ~10 MB each

Monthly partitions, rather than one file, because the nightly job only ever
appends to the current month. It downloads and re-uploads ~10 MB instead of
658 MB, no single file grows without bound no matter how many years
accumulate, and the count of files grows by twelve a year against no limit
worth worrying about. The same property is what makes the published copies
cheap: see src/chain_release.py.

`db_path` still names a real SQLite file, and still holds `stock_prices` and
`precomputed_metrics`. What moved out of it is only the chain payloads. That
keeps resolve_db_path(), SCHWAB_DB_PATH, the mtime cache fingerprint in
src/dashboard/data_loader.py and the overwrite guard in tests/conftest.py
working exactly as they did.

The chain_index table in that file is a derived cache, never the record. It
answers "which dates exist" without opening a partition, and
`rebuild_index()` reconstructs it from the partitions alone. That direction
matters: a corrupt or missing index costs one scan, never a day of chains.

Usage
-----
    db = SchwabDatabase()
    db.append_options_snapshot("SPX", date.today(), chain_df)
    db.append_price_history("SPX", prices_df)
    db.price_series("SPX")
    db.options_snapshot_dates("SPX")
    db.options_snapshot("SPX", date(2026, 7, 25))
    db.contract_history("SPX", "SPX   260821C06900000")
"""

from __future__ import annotations

import os
import json
import sqlite3
from io import StringIO
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pandas as pd

from src import chain_codec

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_DB_PATH = _PROJECT_ROOT / "database" / "schwab_database.db"


def _encode_frame(df: pd.DataFrame) -> str:
    """Serialise a metric frame, recording which columns are datetimes.

    orient="split" alone loses that: a datetime64 column round-trips as
    int64 epoch-milliseconds, so `expiration` came back as 1792108800000
    instead of 2026-10-16 and every date on the page would have rendered as
    a large number. The column names are stored explicitly rather than
    sniffed on read -- guessing which string columns "look like dates" is
    the sort of quiet wrongness that is hard to notice and worse than a
    little verbosity here.

    This is JSON, and JSON is lossy for floats -- a stored 0.3 reads back as
    0.30000000000000004. Harmless here and only here: precomputed_metrics
    holds values derived from the chain, so a demo build that drifts in the
    last bit can be rebuilt from the chain it came from. Chains themselves
    are the only copy that exists and go through src/chain_codec.py, which is
    exact. Do not reach for this function for raw data.
    """
    datetime_columns = [c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])]
    return json.dumps({
        "datetime_columns": datetime_columns,
        "frame": df.to_json(orient="split", date_format="iso", index=False),
    })


def _decode_frame(payload: str) -> pd.DataFrame:
    """Inverse of _encode_frame, restoring datetime dtypes."""
    wrapper = json.loads(payload)
    df = pd.read_json(StringIO(wrapper["frame"]), orient="split")
    for column in wrapper.get("datetime_columns", []):
        if column in df.columns:
            df[column] = pd.to_datetime(df[column])
    return df


_SCHEMA = """
-- Which (symbol, snapshot_date) chains exist, and how many contracts each
-- holds. Derived from the partitions and rebuildable from them -- see
-- rebuild_index(). It exists so "what dates do we have" and "how many
-- symbols did we collect on the 14th" cost one small query instead of
-- opening and decompressing every monthly partition.
CREATE TABLE IF NOT EXISTS chain_index (
    symbol          TEXT    NOT NULL,  -- save_symbol, e.g. "SPX" (the underlying, not the contract)
    snapshot_date   TEXT    NOT NULL,  -- ISO date (UTC) the snapshot was taken
    contracts       INTEGER NOT NULL,  -- rows in the stored chain
    PRIMARY KEY (symbol, snapshot_date)
);
-- snapshot_symbol_counts() groups by date across all symbols, which the
-- primary key's symbol-first ordering cannot serve.
CREATE INDEX IF NOT EXISTS idx_chain_index_date ON chain_index (snapshot_date);

CREATE TABLE IF NOT EXISTS stock_prices (
    symbol      TEXT    NOT NULL,
    price_date  TEXT    NOT NULL,  -- ISO date
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    volume      INTEGER,
    PRIMARY KEY (symbol, price_date)
);
CREATE INDEX IF NOT EXISTS idx_stock_prices_symbol_date ON stock_prices (symbol, price_date);

-- Metric tables (term structure, skew, curvature, VRP) computed ahead of time
-- for one (symbol, snapshot_date). Normally absent: the live dashboard
-- recomputes from the chain it is serving, which is what makes a chain/metrics
-- date mismatch impossible. A frozen demo snapshot has no such risk -- nothing
-- changes after the build -- and recomputation is far too slow there, so the
-- demo builder fills this in. See src/dashboard/data_loader.py.
--
-- One row per metric, payload as JSON rather than five typed tables: the
-- metric frames have different and occasionally changing columns, and a schema
-- per metric would need migrating every time VolatilityMetrics grows one.
CREATE TABLE IF NOT EXISTS precomputed_metrics (
    symbol          TEXT NOT NULL,
    snapshot_date   TEXT NOT NULL,
    metric_name     TEXT NOT NULL,
    payload         TEXT NOT NULL,  -- see _encode_frame: JSON frame + datetime column names
    PRIMARY KEY (symbol, snapshot_date, metric_name)
);
"""

_PARTITION_SCHEMA = """
-- One compressed chain per (symbol, snapshot_date). `contracts` is
-- denormalised here as well as in chain_index so a partition is
-- self-describing: rebuild_index() can reconstruct the index without
-- decompressing a single payload.
CREATE TABLE IF NOT EXISTS chains (
    symbol          TEXT    NOT NULL,
    snapshot_date   TEXT    NOT NULL,
    contracts       INTEGER NOT NULL,
    payload         BLOB    NOT NULL,  -- see src/chain_codec.py
    PRIMARY KEY (symbol, snapshot_date)
);
"""

# The shape every caller of options_snapshot() and contract_history() has
# always received: `SELECT *` against the pre-2026-11-22 `options` table, in
# its declared column order.
#
# This DDL is never created on disk any more. It is executed against an
# in-memory database by _canonical_slice() so that SQLite's own type affinity
# and pandas' own dtype inference -- not a hand-written reimplementation of
# both -- decide what a stored chain looks like. Reproducing "TEXT with nulls
# becomes object, INTEGER with nulls becomes float64, INTEGER without becomes
# int64" by hand is exactly the sort of detail that is wrong in one column and
# noticed a month later, so the rule is to keep asking the thing that defined
# the behaviour in the first place.
_LEGACY_OPTIONS_DDL = """
CREATE TABLE options (
    symbol              TEXT    NOT NULL,
    snapshot_date       TEXT    NOT NULL,
    contract_symbol     TEXT    NOT NULL,  -- OCC-style, e.g. "SPX   260821C06900000"
    option_type         TEXT,
    expiration          TEXT,
    dte                 INTEGER,
    strike_price        REAL,
    bid                 REAL,
    ask                 REAL,
    mark                REAL,
    last                REAL,
    volume              INTEGER,
    open_interest       INTEGER,
    implied_volatility  REAL,
    delta               REAL,
    gamma               REAL,
    theta               REAL,
    vega                REAL,
    rho                 REAL,
    in_the_money        INTEGER,  -- 0/1
    theoretical_value   REAL,
    underlying_price    REAL,
    fetch_time          TEXT,
    PRIMARY KEY (symbol, snapshot_date, contract_symbol)
);
"""

_OPTIONS_COLUMNS = (
    "symbol", "snapshot_date", "contract_symbol", "option_type", "expiration", "dte",
    "strike_price", "bid", "ask", "mark", "last", "volume", "open_interest",
    "implied_volatility", "delta", "gamma", "theta", "vega", "rho",
    "in_the_money", "theoretical_value", "underlying_price", "fetch_time",
)

# Unchanged from the single-table era, deliberately. Historical chains were
# migrated as the exact frames this query produced, so new chains have to be
# produced by the same clause to stay comparable. There is no tiebreak because
# there never was one: a call and a put share an expiration and a strike, so
# their relative order has always been whatever insertion order gave, and
# nothing may depend on it.
_SLICE_ORDER = "ORDER BY expiration, strike_price"

_OPTIONS_CHAIN_COLUMNS = {
    # chain DataFrame column -> options table column
    "symbol": "contract_symbol",
    "optionType": "option_type",
    "expiration": "expiration",
    "dte": "dte",
    "strikePrice": "strike_price",
    "bid": "bid",
    "ask": "ask",
    "mark": "mark",
    "last": "last",
    "volume": "volume",
    "openInterest": "open_interest",
    "impliedVolatility": "implied_volatility",
    "delta": "delta",
    "gamma": "gamma",
    "theta": "theta",
    "vega": "vega",
    "rho": "rho",
    "inTheMoney": "in_the_money",
    "theoreticalOptionValue": "theoretical_value",
    "underlyingPrice": "underlying_price",
    "fetchTime": "fetch_time",
}

_PRICE_COLUMNS = {
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
}


def resolve_db_path(db_path: Path | str | None = None) -> Path:
    """Where the chain database lives, without opening it.

    Callers that only need the path -- a cache fingerprint, a log line --
    must not have to construct a SchwabDatabase, because the constructor
    opens the file and runs DDL against it. Under CI that file used to be an
    unfetched Git LFS pointer, so merely asking where it is raised
    "file is not a database" and took the whole suite down. It is no longer
    in LFS, but the separation is worth keeping: asking where something is
    should not be able to fail.

    SCHWAB_DB_PATH lets a local dev session write somewhere other than the
    default. The chain payloads sit beside whatever path this returns -- see
    resolve_partition_root() -- so pointing it at a scratch file redirects the whole
    store, not half of it.

    Read per call rather than captured at import, so a test or a shell that
    sets the variable later still takes effect.
    """
    env_path = os.environ.get("SCHWAB_DB_PATH")
    return Path(db_path) if db_path else Path(env_path) if env_path else _DEFAULT_DB_PATH


def resolve_partition_root(db_path: Path | str | None = None) -> Path:
    """Directory holding the monthly chain partitions for `db_path`.

    Derived from the path rather than configured separately so the two can
    never be pointed at different stores. A test or demo build that passes
    `db_path=tmp/x.db` gets `tmp/x_chains/`, isolated from the real archive
    with no extra argument to remember.
    """
    resolved = resolve_db_path(db_path)
    return resolved.parent / f"{resolved.stem}_chains"


def store_fingerprint(db_path: Path | str | None = None) -> tuple[tuple[str, int, int], ...]:
    """A value that changes exactly when anything in the store changes.

    Used as a cache key by src/dashboard/data_loader.py and
    src/api/routes.py, so a changed file evicts whatever was derived from it.
    Cheaper and more honest than a TTL, which would either serve stale data
    for its duration or expire data that is still perfectly good.

    It covers the index file *and* every partition, which is the whole point.
    Watching only the file `db_path` names looks right and is not: a chain
    rewritten for a date already present changes a partition while leaving
    the index row byte-identical, and SQLite then does not touch the index
    file at all. The dashboard went on serving the superseded chain until
    someone restarted the server -- the exact silent staleness this key
    exists to prevent.

    Size as well as mtime because a file can be replaced with one of the same
    age: os.replace() of a freshly downloaded partition carries the
    timestamp the server sent.

    Path-based and stat-only, so asking it never opens a database or runs
    DDL. Asking a cache where its data lives must not be able to fail --
    under CI it once did, when the file was an unfetched LFS pointer and
    constructing a SchwabDatabase to find its path raised "file is not a
    database" and took the whole suite down.
    """
    resolved = resolve_db_path(db_path)
    paths = [resolved]
    root = resolve_partition_root(resolved)
    if root.is_dir():
        paths.extend(sorted(root.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].db")))
    marks: list[tuple[str, int, int]] = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            marks.append((path.name, 0, 0))  # absent is a state, not an error
        else:
            marks.append((path.name, stat.st_mtime_ns, stat.st_size))
    return tuple(marks)


def partition_name(snapshot_date: date) -> str:
    """The partition a date belongs to, e.g. date(2026, 11, 17) -> "2026-11"."""
    return f"{snapshot_date.year:04d}-{snapshot_date.month:02d}"


def _canonical_slice(rows: pd.DataFrame | None = None) -> pd.DataFrame:
    """The frame `SELECT * FROM options ...` would have returned for `rows`.

    Also the source of the empty-chain shape: called with nothing, it returns
    the 23 columns with no rows, which is what a query for a date we never
    collected has always produced. Callers index those columns without
    checking, so a bare `pd.DataFrame()` on a miss would turn a missing day
    into a KeyError.
    """
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(_LEGACY_OPTIONS_DDL)
        if rows is not None and not rows.empty:
            conn.executemany(
                f"INSERT INTO options ({', '.join(_OPTIONS_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(_OPTIONS_COLUMNS))})",
                rows[list(_OPTIONS_COLUMNS)].itertuples(index=False, name=None),
            )
        return pd.read_sql_query(f"SELECT * FROM options {_SLICE_ORDER}", conn)
    finally:
        conn.close()


class SchwabDatabase:
    """Accumulator for raw options-chain and stock-price data.

    Prices and metrics live in one SQLite file; chains live as compressed
    blobs in monthly partitions beside it. See the module docstring for why.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = resolve_db_path(db_path)
        self.partition_root = resolve_partition_root(self.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
        self._heal_index()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path))
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Partitions
    # ------------------------------------------------------------------

    def partition_paths(self) -> list[Path]:
        """Every monthly partition that exists, oldest first.

        src/chain_release.py publishes and fetches these individually, which
        is what keeps a nightly run to ~10 MB of transfer.
        """
        if not self.partition_root.is_dir():
            return []
        return sorted(self.partition_root.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].db"))

    def partition_path(self, snapshot_date: date) -> Path:
        """Where the partition for `snapshot_date` is, whether or not it exists."""
        return self.partition_root / f"{partition_name(snapshot_date)}.db"

    @contextmanager
    def _partition_read(self, path: Path):
        """Open a partition without being able to create it.

        sqlite3.connect() on a missing path creates an empty database, which
        would turn "we have no chains for August" into a silently empty
        August that then looks collected. mode=ro raises instead, and the
        caller checks existence first so the normal miss is not an exception.
        """
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _partition_write(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        try:
            conn.executescript(_PARTITION_SCHEMA)
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _heal_index(self) -> None:
        """Rebuild chain_index when it is empty but partitions hold chains.

        That combination means the index and the data arrived separately --
        a freshly downloaded set of partitions, a restored backup, a test
        fixture built by copying files. The index is pure derivation, so
        regenerating it is always safe and always cheaper than the
        alternative, which is a dashboard that reports no history for data
        it is sitting on.
        """
        if not self.partition_paths():
            return
        with self._connect() as conn:
            if conn.execute("SELECT 1 FROM chain_index LIMIT 1").fetchone():
                return
        self.rebuild_index()

    def rebuild_index(self) -> int:
        """Reconstruct chain_index from the partitions. Returns rows written.

        Reads only (symbol, snapshot_date, contracts) -- no payload is
        decompressed -- so this is a metadata scan even across years of
        archive. The index is replaced wholesale rather than merged: a
        partition that was deleted should disappear from the index, and a
        merge would keep claiming it forever.
        """
        rows: list[tuple[str, str, int]] = []
        for path in self.partition_paths():
            with self._partition_read(path) as conn:
                rows.extend(
                    conn.execute("SELECT symbol, snapshot_date, contracts FROM chains").fetchall()
                )
        with self._connect() as conn:
            conn.execute("DELETE FROM chain_index")
            conn.executemany(
                "INSERT OR REPLACE INTO chain_index (symbol, snapshot_date, contracts) "
                "VALUES (?, ?, ?)",
                rows,
            )
        return len(rows)

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def append_options_snapshot(self, symbol: str, snapshot_date: date, chain: pd.DataFrame) -> int:
        """
        Store `chain` as the snapshot for (symbol, snapshot_date), replacing
        any chain already held for that key. Safe to call more than once for
        the same day -- matching HistoryStore's convention. Returns the
        number of contract rows written (0 if `chain` is empty).
        """
        if chain is None or chain.empty:
            return 0

        df = pd.DataFrame({
            dest: chain[src] for src, dest in _OPTIONS_CHAIN_COLUMNS.items() if src in chain.columns
        })
        df["symbol"] = symbol
        df["snapshot_date"] = snapshot_date.isoformat()
        if "expiration" in df.columns:
            df["expiration"] = pd.to_datetime(df["expiration"]).dt.strftime("%Y-%m-%d")
        if "fetch_time" in df.columns:
            df["fetch_time"] = pd.to_datetime(df["fetch_time"]).astype(str)
        if "in_the_money" in df.columns:
            df["in_the_money"] = df["in_the_money"].astype(bool).astype(int)
        for col in _OPTIONS_COLUMNS:
            if col not in df.columns:
                df[col] = None

        # Through the legacy DDL on the way in, so what is stored is what the
        # old SELECT would have handed back -- see _canonical_slice.
        canonical = _canonical_slice(df)
        payload = chain_codec.encode(canonical)

        with self._partition_write(self.partition_path(snapshot_date)) as conn:
            conn.execute(
                "INSERT INTO chains (symbol, snapshot_date, contracts, payload) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(symbol, snapshot_date) DO UPDATE SET "
                "contracts=excluded.contracts, payload=excluded.payload",
                (symbol, snapshot_date.isoformat(), len(canonical), payload),
            )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO chain_index (symbol, snapshot_date, contracts) VALUES (?, ?, ?) "
                "ON CONFLICT(symbol, snapshot_date) DO UPDATE SET contracts=excluded.contracts",
                (symbol, snapshot_date.isoformat(), len(canonical)),
            )
        return len(canonical)

    def delete_options_snapshots(self, keep: list[date] | None = None) -> int:
        """Drop stored chains, optionally keeping the dates in `keep`.

        Used by src/build_demo_db.py to cut the archive down to the handful
        of dates a demo needs. Whole partitions that keep nothing are
        deleted outright rather than emptied, because an emptied SQLite file
        still occupies its pages and the point of the operation is size.
        Returns the number of chains removed.
        """
        keep_iso = {d.isoformat() for d in (keep or [])}
        removed = 0
        for path in self.partition_paths():
            with self._partition_read(path) as conn:
                dates = {r[0] for r in conn.execute("SELECT DISTINCT snapshot_date FROM chains")}
            doomed = dates - keep_iso
            if not doomed:
                continue
            if dates == doomed:
                with self._partition_read(path) as conn:
                    removed += conn.execute("SELECT COUNT(*) FROM chains").fetchone()[0]
                path.unlink()
                continue
            with self._partition_write(path) as conn:
                placeholders = ", ".join("?" * len(doomed))
                removed += conn.execute(
                    f"DELETE FROM chains WHERE snapshot_date IN ({placeholders})",
                    tuple(sorted(doomed)),
                ).rowcount
                conn.commit()
                conn.execute("VACUUM")
        self.rebuild_index()
        return removed

    def relabel_snapshot_date(self, stored: date, destination: date) -> int:
        """Move every chain stored under `stored` to `destination`.

        src/backfill_session_dates.py needs this: a run that straddled
        midnight UTC filed a session under the following calendar day, and
        the fix is to re-file it rather than to re-collect data Schwab no
        longer serves. A chain already present under `destination` wins --
        it is the one collected on purpose -- and the misfiled copy is
        dropped. Returns the number of chains moved.
        """
        if stored == destination:
            return 0
        source_path = self.partition_path(stored)
        if not source_path.exists():
            return 0

        with self._partition_read(source_path) as conn:
            rows = conn.execute(
                "SELECT symbol, contracts, payload FROM chains WHERE snapshot_date = ?",
                (stored.isoformat(),),
            ).fetchall()
        if not rows:
            return 0

        # Read what the destination already holds before writing anything, so
        # "destination wins" is decided against the pre-existing state even
        # when both dates land in the same partition.
        dest_path = self.partition_path(destination)
        existing: set[str] = set()
        if dest_path.exists():
            with self._partition_read(dest_path) as conn:
                existing = {
                    r[0] for r in conn.execute(
                        "SELECT symbol FROM chains WHERE snapshot_date = ?",
                        (destination.isoformat(),),
                    )
                }
        moving = [r for r in rows if r[0] not in existing]
        with self._partition_write(dest_path) as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO chains (symbol, snapshot_date, contracts, payload) "
                "VALUES (?, ?, ?, ?)",
                [(symbol, destination.isoformat(), contracts, payload)
                 for symbol, contracts, payload in moving],
            )
        with self._partition_write(source_path) as conn:
            conn.execute("DELETE FROM chains WHERE snapshot_date = ?", (stored.isoformat(),))
            conn.commit()
            if not conn.execute("SELECT 1 FROM chains LIMIT 1").fetchone():
                drop_source = True
            else:
                drop_source = False
                conn.execute("VACUUM")
        if drop_source:
            source_path.unlink()
        self.rebuild_index()
        return len(moving)

    def append_price_history(self, symbol: str, prices: pd.DataFrame) -> int:
        """
        Upsert one row per trading day in `prices` (job.py's fetched daily
        OHLCV) for `symbol`. Safe to call repeatedly -- REPLACEs rather than
        duplicates, so re-fetching the same trailing window every run
        doesn't create duplicate rows, and a later run's revised close
        (e.g. same-day intraday re-fetch) overwrites the earlier one.
        """
        if prices is None or prices.empty or "datetime" not in prices.columns:
            return 0

        df = pd.DataFrame({
            dest: prices[src] for src, dest in _PRICE_COLUMNS.items() if src in prices.columns
        })
        df["symbol"] = symbol
        df["price_date"] = pd.to_datetime(prices["datetime"]).dt.strftime("%Y-%m-%d")

        columns = ["symbol", "price_date", "open", "high", "low", "close", "volume"]
        for col in columns:
            if col not in df.columns:
                df[col] = None
        df = df[columns]

        placeholders = ", ".join("?" * len(columns))
        with self._connect() as conn:
            conn.executemany(
                f"""
                INSERT INTO stock_prices ({", ".join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(symbol, price_date) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low,
                    close=excluded.close, volume=excluded.volume
                """,
                df.itertuples(index=False, name=None),
            )
        return len(df)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def price_series(self, symbol: str, start: date | None = None, end: date | None = None) -> pd.DataFrame:
        """Daily OHLCV close-price history for `symbol`, ascending by date, optionally bounded."""
        clauses = ["symbol = ?"]
        params: list[str] = [symbol]
        if start is not None:
            clauses.append("price_date >= ?")
            params.append(start.isoformat())
        if end is not None:
            clauses.append("price_date <= ?")
            params.append(end.isoformat())
        with self._connect() as conn:
            return pd.read_sql_query(
                f"SELECT * FROM stock_prices WHERE {' AND '.join(clauses)} ORDER BY price_date",
                conn,
                params=params,
            )

    def snapshot_symbol_counts(self, limit: int = 8) -> list[tuple[date, int]]:
        """How many distinct symbols the raw chain holds per snapshot date,
        most recent first.

        Exists so src/validate_collection.py can cross-check the chains
        against history/vol_history.db. The two are written by the same run
        and should agree; on 2026-09-14 they didn't, because this store's
        date was computed per symbol while the other's was passed in once for
        the whole run, and the run straddled midnight UTC. Everything that
        only read metric_history saw a healthy 24-symbol day and reported
        success.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT snapshot_date, COUNT(DISTINCT symbol)
                FROM chain_index
                GROUP BY snapshot_date
                ORDER BY snapshot_date DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [(date.fromisoformat(r[0]), r[1]) for r in rows]

    def options_snapshot_dates(self, symbol: str) -> list[date]:
        """Every date we have a full stored options-chain snapshot for this symbol, ascending."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT snapshot_date FROM chain_index WHERE symbol = ? ORDER BY snapshot_date",
                (symbol,),
            ).fetchall()
        return [date.fromisoformat(r[0]) for r in rows]

    def options_snapshot(self, symbol: str, snapshot_date: date) -> pd.DataFrame:
        """The full stored chain for `symbol` on one specific historical date.

        An absent chain returns the empty 23-column frame rather than
        raising: "we did not collect that day" is a normal answer here, and
        every caller already handles an empty chain.
        """
        path = self.partition_path(snapshot_date)
        if not path.exists():
            return _canonical_slice()
        with self._partition_read(path) as conn:
            row = conn.execute(
                "SELECT payload FROM chains WHERE symbol = ? AND snapshot_date = ?",
                (symbol, snapshot_date.isoformat()),
            ).fetchone()
        return chain_codec.decode(row[0]) if row else _canonical_slice()

    def contract_history(self, symbol: str, contract_symbol: str) -> pd.DataFrame:
        """Day-over-day history for one specific contract (e.g. its IV/volume/OI over time).

        This is the one read the storage layout does not favour: it crosses
        every date instead of selecting one, so it decodes each of the
        symbol's chains and keeps a single row from each. In exchange, the
        80 MB index that used to serve it is gone -- it existed for this
        method alone, and the frontend has never called the endpoint built
        on it. One symbol's full archive decodes in well under a second, so
        the trade is a lot of permanent storage for a little latency on a
        cold path.
        """
        frames = [
            frame[frame["contract_symbol"] == contract_symbol]
            for frame in (
                self.options_snapshot(symbol, day) for day in self.options_snapshot_dates(symbol)
            )
        ]
        present = [f for f in frames if not f.empty]
        if not present:
            return _canonical_slice()
        return (
            pd.concat(present, ignore_index=True)
            .sort_values("snapshot_date", kind="stable")
            .reset_index(drop=True)
        )

    def write_precomputed_metrics(self, symbol: str, snapshot_date: date, metrics: dict) -> int:
        """Store already-computed metric frames for one (symbol, date)."""
        rows = [
            (symbol, snapshot_date.isoformat(), name, _encode_frame(df))
            for name, df in metrics.items()
            if df is not None and not df.empty
        ]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO precomputed_metrics "
                "(symbol, snapshot_date, metric_name, payload) VALUES (?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def precomputed_metrics(self, symbol: str, snapshot_date: date) -> dict:
        """Stored metric frames for one (symbol, date), or {} when absent.

        Absent is the normal case and not an error -- only a demo build fills
        this table. Returning {} lets the caller fall through to computing
        them, so the same code path serves both.
        """
        with self._connect() as conn:
            try:
                rows = conn.execute(
                    "SELECT metric_name, payload FROM precomputed_metrics "
                    "WHERE symbol = ? AND snapshot_date = ?",
                    (symbol, snapshot_date.isoformat()),
                ).fetchall()
            except sqlite3.OperationalError:
                return {}  # table predates this feature
        return {name: _decode_frame(payload) for name, payload in rows}

    def latest_options_snapshot_date(self, symbol: str) -> date | None:
        """The most recent date with a stored chain for `symbol`, or None.

        A MAX() rather than the last element of options_snapshot_dates():
        the dashboard asks this once per symbol per cold leaderboard request
        (24 of them), and it should cost a single index probe.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(snapshot_date) FROM chain_index WHERE symbol = ?", (symbol,)
            ).fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None
