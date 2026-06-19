"""
src/data_store.py

CSVStore — persists and loads options chain snapshots and computed metrics.

Directory layout (under base_dir, default: <project_root>/data/):
    raw/        SPX_chain_20240119.csv
                SPX_prices_20240119.csv
    processed/  SPX_skew_20240119.csv
                SPX_vrp_20240119.csv

Usage
-----
    store = CSVStore(symbol="SPX")
    store.save_chain(chain_df)
    chain = store.load_latest_chain()
"""

from __future__ import annotations

from datetime import datetime, UTC
from pathlib import Path
import re

import pandas as pd

_PROJECT_ROOT = Path(__file__).parent.parent


class CSVStore:
    """CSV-based persistence for raw snapshots and computed metrics."""

    def __init__(
        self,
        symbol: str = "SPX",
        base_dir: Path | None = None,
    ) -> None:
        self.symbol = symbol
        base = Path(base_dir) if base_dir else _PROJECT_ROOT / "data"
        self._raw_dir = base / "raw"
        self._processed_dir = base / "processed"

    # ------------------------------------------------------------------
    # Raw chain
    # ------------------------------------------------------------------

    def save_chain(self, df: pd.DataFrame) -> Path:
        """Persist a chain snapshot as CSV. Returns the saved path."""
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        path = self._raw_dir / f"{self.symbol}_chain_{self._day_stamp()}.csv"
        df.to_csv(path, index=False)
        print(f"[CSVStore] Saved chain  ({len(df):,} rows) → {path.relative_to(_PROJECT_ROOT)}")
        return path

    def load_latest_chain(self) -> pd.DataFrame:
        """Load the most recent chain snapshot."""
        return self._read_csv(self._latest("chain"))

    def load_all_chains(self) -> pd.DataFrame:
        """Concatenate all saved chain snapshots (full history)."""
        files = self.list_snapshots()
        if not files:
            raise FileNotFoundError(f"No chain snapshots for '{self.symbol}'.")
        df = pd.concat([self._read_csv(f) for f in files], ignore_index=True)
        print(f"[CSVStore] Loaded {len(files)} chain snapshot(s) for {self.symbol}")
        return df

    def list_snapshots(self) -> list[Path]:
        """Sorted list of all chain snapshot paths."""
        return sorted(self._raw_dir.glob(f"{self.symbol}_chain_*.csv"))

    # ------------------------------------------------------------------
    # Price history
    # ------------------------------------------------------------------

    def save_price_history(self, df: pd.DataFrame) -> Path:
        """Persist a price history DataFrame as CSV. Returns the saved path."""
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        path = self._raw_dir / f"{self.symbol}_prices_{self._day_stamp()}.csv"
        df.to_csv(path, index=False)
        print(f"[CSVStore] Saved prices ({len(df):,} rows) → {path.relative_to(_PROJECT_ROOT)}")
        return path

    def load_latest_price_history(self) -> pd.DataFrame:
        """Load the most recent price history snapshot."""
        return self._read_csv(self._latest("prices"))

    # ------------------------------------------------------------------
    # Computed metrics
    # ------------------------------------------------------------------

    def save_metrics(self, df: pd.DataFrame, metric_name: str) -> Path:
        """Persist a metrics DataFrame as CSV under data/processed/. Returns the saved path."""
        self._processed_dir.mkdir(parents=True, exist_ok=True)
        path = self._processed_dir / f"{self.symbol}_{metric_name}_{self._day_stamp()}.csv"
        df.to_csv(path, index=False)
        print(f"[CSVStore] Saved metric '{metric_name}' → {path.relative_to(_PROJECT_ROOT)}")
        return path

    def load_latest_metrics(self, metric_name: str) -> pd.DataFrame:
        """Load the most recent metrics file for the given metric name."""
        return self._read_csv(self._latest_metric(metric_name))

    def load_all_metrics(self, metric_name: str) -> pd.DataFrame:
        """Concatenate all saved metric snapshots for time-series analysis."""
        files = self._metric_files(metric_name)
        if not files:
            raise FileNotFoundError(f"No '{metric_name}' metrics for '{self.symbol}'.")
        return pd.concat([self._read_csv(f) for f in files], ignore_index=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _day_stamp() -> str:
        """UTC day key used for daily overwrite behavior (YYYYMMDD)."""
        return datetime.now(UTC).strftime("%Y%m%d")

    @staticmethod
    def _read_csv(path: Path) -> pd.DataFrame:
        """Read CSV and parse known datetime columns when present."""
        df = pd.read_csv(path)
        for col in ("expiration", "fetchTime", "datetime"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
        return df

    def _latest(self, kind: str) -> Path:
        files = sorted(self._raw_dir.glob(f"{self.symbol}_{kind}_*.csv"))
        if not files:
            raise FileNotFoundError(
                f"No saved '{kind}' files for '{self.symbol}' in {self._raw_dir}."
            )
        return max(files, key=lambda p: p.stat().st_mtime)

    def _latest_metric(self, metric_name: str) -> Path:
        files = self._metric_files(metric_name)
        if not files:
            raise FileNotFoundError(
                f"No saved metric '{metric_name}' for '{self.symbol}'."
            )
        return max(files, key=lambda p: p.stat().st_mtime)

    def _metric_files(self, metric_name: str) -> list[Path]:
        """Return metric files for new daily and legacy timestamp naming formats."""
        escaped_symbol = re.escape(self.symbol)
        escaped_metric = re.escape(metric_name)
        pattern = re.compile(
            rf"^{escaped_symbol}_{escaped_metric}_\d{{8}}(?:_\d{{6}})?\.csv$"
        )
        files = [p for p in self._processed_dir.glob("*.csv") if pattern.match(p.name)]
        return sorted(files)


# Backward-compatible alias used by existing imports in the repo.
ParquetStore = CSVStore
