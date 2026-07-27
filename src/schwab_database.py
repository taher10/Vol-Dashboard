"""
src/schwab_database.py

SchwabDatabase -- SQLite-backed, append-only accumulation of raw daily
options-chain and stock-close-price data, one row per contract-per-day and
one row per symbol-per-day respectively. Lives at database/schwab_database.db.

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

This is deliberately committed to git (like history/vol_history.db), not
gitignored -- see README's "Web app" section for the size-growth tradeoff
that comes with that choice for a table this much larger than
metric_history's.

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

import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_DB_PATH = _PROJECT_ROOT / "database" / "schwab_database.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS options (
    symbol              TEXT    NOT NULL,  -- save_symbol, e.g. "SPX" (the underlying, not the contract)
    snapshot_date       TEXT    NOT NULL,  -- ISO date (UTC) the snapshot was taken
    contract_symbol     TEXT    NOT NULL,  -- OCC-style contract symbol, e.g. "SPX   260821C06900000"
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
CREATE INDEX IF NOT EXISTS idx_options_symbol_date ON options (symbol, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_options_contract ON options (symbol, contract_symbol);

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
"""

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


class SchwabDatabase:
    """SQLite-backed accumulator for raw options-chain and stock-price data."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path))
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def append_options_snapshot(self, symbol: str, snapshot_date: date, chain: pd.DataFrame) -> int:
        """
        Upsert one row per contract in `chain` for (symbol, snapshot_date).
        Safe to call more than once for the same day -- REPLACEs rather than
        duplicates, matching HistoryStore's convention. Returns the number
        of contract rows written (0 if `chain` is empty).
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

        columns = [
            "symbol", "snapshot_date", "contract_symbol", "option_type", "expiration", "dte",
            "strike_price", "bid", "ask", "mark", "last", "volume", "open_interest",
            "implied_volatility", "delta", "gamma", "theta", "vega", "rho",
            "in_the_money", "theoretical_value", "underlying_price", "fetch_time",
        ]
        for col in columns:
            if col not in df.columns:
                df[col] = None
        df = df[columns]

        placeholders = ", ".join("?" * len(columns))
        update_clause = ", ".join(f"{c}=excluded.{c}" for c in columns if c not in ("symbol", "snapshot_date", "contract_symbol"))
        with self._connect() as conn:
            conn.executemany(
                f"""
                INSERT INTO options ({", ".join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(symbol, snapshot_date, contract_symbol) DO UPDATE SET {update_clause}
                """,
                df.itertuples(index=False, name=None),
            )
        return len(df)

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

    def options_snapshot_dates(self, symbol: str) -> list[date]:
        """Every date we have a full stored options-chain snapshot for this symbol, ascending."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT snapshot_date FROM options WHERE symbol = ? ORDER BY snapshot_date",
                (symbol,),
            ).fetchall()
        return [date.fromisoformat(r[0]) for r in rows]

    def options_snapshot(self, symbol: str, snapshot_date: date) -> pd.DataFrame:
        """The full stored chain for `symbol` on one specific historical date."""
        with self._connect() as conn:
            return pd.read_sql_query(
                "SELECT * FROM options WHERE symbol = ? AND snapshot_date = ? ORDER BY expiration, strike_price",
                conn,
                params=(symbol, snapshot_date.isoformat()),
            )

    def contract_history(self, symbol: str, contract_symbol: str) -> pd.DataFrame:
        """Day-over-day history for one specific contract (e.g. its IV/volume/OI over time)."""
        with self._connect() as conn:
            return pd.read_sql_query(
                "SELECT * FROM options WHERE symbol = ? AND contract_symbol = ? ORDER BY snapshot_date",
                conn,
                params=(symbol, contract_symbol),
            )
