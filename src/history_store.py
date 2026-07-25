"""
src/history_store.py

HistoryStore -- SQLite-backed, append-only accumulation of daily per-expiry
vol metrics (atm_iv, skew, curvature, realized_vol, vrp), one row per
(symbol, snapshot_date, expiration).

Why this exists, and how it differs from CSVStore
---------------------------------------------------
CSVStore (data_store.py) persists to data/, which is gitignored and
overwrites same-day files -- it only ever answers "what does today's
snapshot look like." IV Rank, a metric's z-score against its own trailing
distribution, and a day-over-day digest all need real accumulated history,
which nothing before this module provided (confirmed: the deployed app's
disk is ephemeral, and locally we only ever had whichever single days
someone happened to run the pipeline on).

This database is meant to be committed to git by a scheduled job (see
.github/workflows/daily-snapshot.yml and src/daily_snapshot.py) precisely
so that history survives the ephemeral disk and actually accumulates --
git-committed SQLite, not a hosted database, per the deliberate choice to
avoid a new external service for a single-user tool.

Usage
-----
    store = HistoryStore()
    store.append_snapshot("SPX", date.today(), metrics)   # upsert, safe to rerun same day
    store.iv_rank("SPX")
    store.trailing_zscore("SPX", "skew")
    store.prior_snapshot("SPX", days_ago=1)
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_DB_PATH = _PROJECT_ROOT / "history" / "vol_history.db"

_METRIC_COLUMNS = ("atm_iv", "skew", "curvature", "realized_vol", "vrp")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS metric_history (
    symbol         TEXT    NOT NULL,
    snapshot_date  TEXT    NOT NULL,  -- ISO date (UTC) the snapshot was taken
    expiration     TEXT    NOT NULL,  -- ISO date of the option expiry
    dte            INTEGER NOT NULL,
    atm_iv         REAL,
    skew           REAL,
    curvature      REAL,
    realized_vol   REAL,
    vrp            REAL,
    PRIMARY KEY (symbol, snapshot_date, expiration)
);
CREATE INDEX IF NOT EXISTS idx_metric_history_symbol_date
    ON metric_history (symbol, snapshot_date);
"""


class HistoryStore:
    """SQLite-backed accumulator for daily per-expiry vol metrics."""

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

    def append_snapshot(
        self,
        symbol: str,
        snapshot_date: date,
        metrics: dict[str, pd.DataFrame],
    ) -> int:
        """
        Merge one symbol's term_structure/skew/curvature/vrp DataFrames
        (VolatilityMetrics.compute_all() output, same shape job.py already
        produces) into a single row per expiration and upsert into
        metric_history. Safe to call more than once for the same
        (symbol, snapshot_date) -- REPLACEs rather than duplicates, so a
        same-day rerun of the pipeline doesn't create a second row.
        Returns the number of expiration rows written.
        """
        term = metrics.get("term_structure")
        if term is None or term.empty:
            return 0

        merged = term[["expiration", "dte", "atm_iv"]].copy()

        skew = metrics.get("skew")
        if skew is not None and not skew.empty and "skew" in skew.columns:
            merged = merged.merge(skew[["expiration", "dte", "skew"]], on=["expiration", "dte"], how="left")
        else:
            merged["skew"] = None

        curvature = metrics.get("curvature")
        if curvature is not None and not curvature.empty and "curvature" in curvature.columns:
            merged = merged.merge(
                curvature[["expiration", "dte", "curvature"]], on=["expiration", "dte"], how="left"
            )
        else:
            merged["curvature"] = None

        vrp = metrics.get("vrp")
        if vrp is not None and not vrp.empty:
            cols = [c for c in ("realized_vol", "vrp") if c in vrp.columns]
            if cols:
                merged = merged.merge(vrp[["expiration", "dte", *cols]], on=["expiration", "dte"], how="left")
        for col in ("realized_vol", "vrp"):
            if col not in merged.columns:
                merged[col] = None

        merged["symbol"] = symbol
        merged["snapshot_date"] = snapshot_date.isoformat()
        merged["expiration"] = pd.to_datetime(merged["expiration"]).dt.strftime("%Y-%m-%d")

        rows = merged[
            ["symbol", "snapshot_date", "expiration", "dte", *_METRIC_COLUMNS]
        ].itertuples(index=False, name=None)

        with self._connect() as conn:
            conn.executemany(
                f"""
                INSERT INTO metric_history
                    (symbol, snapshot_date, expiration, dte, {", ".join(_METRIC_COLUMNS)})
                VALUES (?, ?, ?, ?, {", ".join("?" * len(_METRIC_COLUMNS))})
                ON CONFLICT(symbol, snapshot_date, expiration) DO UPDATE SET
                    dte=excluded.dte,
                    atm_iv=excluded.atm_iv,
                    skew=excluded.skew,
                    curvature=excluded.curvature,
                    realized_vol=excluded.realized_vol,
                    vrp=excluded.vrp
                """,
                list(rows),
            )
        return len(merged)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def snapshot_dates(self, symbol: str) -> list[date]:
        """Every date we have a stored snapshot for this symbol, ascending."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT snapshot_date FROM metric_history WHERE symbol = ? ORDER BY snapshot_date",
                (symbol,),
            ).fetchall()
        return [date.fromisoformat(r[0]) for r in rows]

    def _nearest_dte_series(
        self, symbol: str, target_dte: int, lookback_days: int, metric_col: str
    ) -> pd.DataFrame:
        """
        For each stored snapshot_date within lookback_days, pick whichever
        expiration was closest to target_dte that day (listed expiries
        shift daily, so this is what makes day-to-day comparison apples-to-
        apples -- the standard "constant maturity" convention IV Rank/
        Percentile is built on) and return (snapshot_date, metric value).
        """
        cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
        with self._connect() as conn:
            df = pd.read_sql_query(
                f"""
                SELECT snapshot_date, dte, {metric_col}
                FROM metric_history
                WHERE symbol = ? AND snapshot_date >= ? AND {metric_col} IS NOT NULL
                """,
                conn,
                params=(symbol, cutoff),
            )
        if df.empty:
            return df
        df["dte_distance"] = (df["dte"] - target_dte).abs()
        nearest = df.sort_values(["snapshot_date", "dte_distance"]).groupby("snapshot_date").first()
        return nearest.reset_index()[["snapshot_date", metric_col]]

    def iv_rank(
        self, symbol: str, target_dte: int = 30, lookback_days: int = 365
    ) -> dict | None:
        """
        IV Rank and IV Percentile for `symbol`'s constant-~target_dte ATM IV,
        over the trailing lookback_days.

        IV Rank      = (current - min) / (max - min) * 100  -- where today
                        sits in the observed range (ThinkOrSwim convention).
        IV Percentile = % of historical trading days with IV below today's --
                        more robust to a single outlier min/max than Rank.

        Returns None if there's no history yet for this symbol (needs at
        least 2 stored snapshot dates including today) -- meaningful only
        after the daily job has been running for a while.
        """
        series = self._nearest_dte_series(symbol, target_dte, lookback_days, "atm_iv")
        if len(series) < 2:
            return None

        current = series.iloc[-1]["atm_iv"]
        history = series["atm_iv"]
        lo, hi = history.min(), history.max()
        rank = 50.0 if hi == lo else (current - lo) / (hi - lo) * 100.0
        percentile = (history < current).mean() * 100.0

        return {
            "current_iv": float(current),
            "iv_rank": float(rank),
            "iv_percentile": float(percentile),
            "lookback_low": float(lo),
            "lookback_high": float(hi),
            "n_observations": len(series),
        }

    def trailing_zscore(
        self, symbol: str, metric_col: str, target_dte: int = 30, lookback_days: int = 90
    ) -> dict | None:
        """
        Z-score of `symbol`'s current constant-~target_dte `metric_col`
        reading against its own trailing lookback_days distribution --
        "is -3 skew normal or extreme for this name" needs this, a
        cross-sectional (across-expiries, same day) z-score can't answer it.
        Returns None without at least 3 historical points (not enough to
        estimate a meaningful std dev) or zero variance.
        """
        if metric_col not in _METRIC_COLUMNS:
            raise ValueError(f"Unknown metric column: {metric_col!r}")
        series = self._nearest_dte_series(symbol, target_dte, lookback_days, metric_col)
        if len(series) < 3:
            return None

        current = series.iloc[-1][metric_col]
        history = series[metric_col]
        std = history.std()
        if not std or pd.isna(std):
            return None

        return {
            "current_value": float(current),
            "trailing_mean": float(history.mean()),
            "trailing_std": float(std),
            "zscore": float((current - history.mean()) / std),
            "n_observations": len(series),
        }

    def prior_snapshot(self, symbol: str, days_ago: int = 1) -> pd.DataFrame | None:
        """
        The full per-expiry metrics row-set from the Nth most recent stored
        snapshot before today (days_ago=1 -> the previous stored date, not
        literally "yesterday" -- weekends/gaps in the job schedule mean the
        prior stored date may be more than 24h back). Returns None if there
        aren't that many snapshots yet.
        """
        dates = self.snapshot_dates(symbol)
        if len(dates) <= days_ago:
            return None
        target_date = dates[-1 - days_ago]
        with self._connect() as conn:
            return pd.read_sql_query(
                "SELECT * FROM metric_history WHERE symbol = ? AND snapshot_date = ? ORDER BY dte",
                conn,
                params=(symbol, target_date.isoformat()),
            )
