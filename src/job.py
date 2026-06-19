"""
src/job.py

OptionsVolJob — orchestrates the full pipeline:
    1. Authenticate with Schwab
    2. Fetch SPX options chain + price history
    3. Persist raw snapshots (CSV)
    4. Compute all volatility metrics
    5. Persist computed metrics (CSV)

Usage — as a script (CLI):
    python -m src.job                        # default: $SPX, save symbol SPX
    python -m src.job --symbol $SPX --first-time   # first-time OAuth flow

Usage — as a library:
    from src.job import OptionsVolJob
    job = OptionsVolJob(symbol="$SPX")
    results = job.run()            # dict[str, pd.DataFrame]
    historical = job.backfill()    # loads all snapshots, recomputes metrics
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, UTC
from pathlib import Path
from typing import Optional

# Ensure project root is on sys.path regardless of how this file is invoked
# (terminal, VS Code Run button, debugger, python -m, etc.)
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd

from src.auth import SchwabAuth
from src.data_store import CSVStore
from src.metrics import VolatilityMetrics
from src.options_fetcher import OptionsFetcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("OptionsVolJob")


class OptionsVolJob:
    """
    End-to-end pipeline for fetching SPX options data and computing vol metrics.

    Parameters
    ----------
    symbol      : Schwab API symbol (e.g. '$SPX')
    save_symbol : Label used in CSV filenames (e.g. 'SPX')
    target_delta: Delta used for skew/butterfly computations (default 0.25)
    rv_window   : Realized vol lookback in trading days (default 21 ≈ 1 month)
    data_dir    : Override the data directory (default: <project_root>/data)
    """

    def __init__(
        self,
        symbol: str = "$SPX",
        save_symbol: str = "SPX",
        target_delta: float = 0.25,
        rv_window: int = 21,
        data_dir: Optional[Path] = None,
    ) -> None:
        self.symbol = symbol
        self.save_symbol = save_symbol
        self.target_delta = target_delta
        self.rv_window = rv_window
        self._auth = SchwabAuth.from_env()
        # CSVStore uses a day-based filename key, so reruns on the same UTC day overwrite.
        self._store = CSVStore(symbol=save_symbol, base_dir=data_dir)

    # ------------------------------------------------------------------
    # Primary entry point
    # ------------------------------------------------------------------

    def run(self) -> dict[str, pd.DataFrame]:
        """
        Execute the full daily pipeline:
          authenticate → fetch chain → fetch prices → save raw →
          compute all metrics → save metrics.

        Returns a dict of metrics DataFrames keyed by metric name.
        """
        logger.info("=== OptionsVolJob.run() | %s | %s ===",
                    self.symbol, datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"))

        # 1. Auth
        client = self._auth.get_client()
        logger.info("Authenticated ✓")

        # 2. Fetch
        fetcher = OptionsFetcher(client, symbol=self.symbol)

        logger.info("Fetching monthly SPX options chain (±5 strikes ATM per expiry) ...")
        chain = fetcher.fetch_monthly_chain(strikes_each_side=5)
        logger.info("Chain: %d contracts, %d expirations",
                    len(chain), chain["expiration"].nunique())

        logger.info("Fetching price history ...")
        prices = fetcher.fetch_price_history()
        logger.info("Prices: %d rows (%s → %s)",
                    len(prices),
                    prices["datetime"].iloc[0].date(),
                    prices["datetime"].iloc[-1].date())

        # 3. Persist raw
        self._store.save_chain(chain)
        self._store.save_price_history(prices)

        # 4. Compute metrics
        logger.info("Computing volatility metrics ...")
        vm = VolatilityMetrics(chain, price_history=prices)
        results = vm.compute_all(target_delta=self.target_delta, rv_window=self.rv_window)

        # 5. Persist metrics
        for name, df in results.items():
            self._store.save_metrics(df, metric_name=name)

        logger.info("Pipeline complete. Metrics: %s", list(results.keys()))
        return results

    # ------------------------------------------------------------------
    # First-time authentication
    # ------------------------------------------------------------------

    def authenticate(self) -> None:
        """
        Run the one-time Schwab OAuth browser flow.
        Call this once per environment — then use run() for all subsequent runs.
        """
        logger.info("Starting OAuth browser flow for %s ...", self.symbol)
        self._auth.authenticate()
        logger.info("Authentication complete. Token saved.")

    # ------------------------------------------------------------------
    # Historical backfill
    # ------------------------------------------------------------------

    def backfill(self) -> dict[str, pd.DataFrame]:
        """
        Load ALL saved chain snapshots from disk, recompute every metric,
        and persist one consolidated metrics file per metric.

        Useful after adding a new metric or fixing a computation bug.
        Returns the metrics computed from the most recent snapshot.
        """
        logger.info("Backfilling metrics from all saved snapshots ...")
        chain = self._store.load_all_chains()
        try:
            prices = self._store.load_latest_price_history()
        except FileNotFoundError:
            logger.warning("No price history found; VRP will be skipped.")
            prices = None

        snapshots = chain["fetchTime"].unique() if "fetchTime" in chain.columns else [None]
        logger.info("Found %d chain rows across all snapshots.", len(chain))

        # Recompute on the full aggregated chain (useful for time-series analysis)
        vm = VolatilityMetrics(chain, price_history=prices)
        results = vm.compute_all(target_delta=self.target_delta, rv_window=self.rv_window)
        for name, df in results.items():
            self._store.save_metrics(df, metric_name=f"{name}_backfill")

        logger.info("Backfill complete.")
        return results

    # ------------------------------------------------------------------
    # Quick inspection helpers
    # ------------------------------------------------------------------

    def latest_skew(self) -> pd.DataFrame:
        """Return the most recently saved skew DataFrame."""
        return self._store.load_latest_metrics("skew")

    def latest_term_structure(self) -> pd.DataFrame:
        """Return the most recently saved ATM IV term structure."""
        return self._store.load_latest_metrics("term_structure")

    def latest_vrp(self) -> pd.DataFrame:
        """Return the most recently saved VRP DataFrame."""
        return self._store.load_latest_metrics("vrp")

    def snapshot_count(self) -> int:
        """How many chain snapshots have been saved so far."""
        return len(self._store.list_snapshots())


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="SPX Options Volatility Database — Schwab API pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--symbol", default="$SPX", help="Schwab symbol to pull (e.g. $SPX, $NDX)")
    p.add_argument("--save-symbol", default="SPX", help="Label used in CSV filenames")
    p.add_argument("--delta", type=float, default=0.25, help="Target delta for skew metrics")
    p.add_argument("--rv-window", type=int, default=21, help="Realized vol lookback (trading days)")
    p.add_argument(
        "--first-time",
        action="store_true",
        help="Run OAuth browser flow (first-time setup only)",
    )
    p.add_argument(
        "--backfill",
        action="store_true",
        help="Recompute all metrics from saved snapshots",
    )
    return p


if __name__ == "__main__":
    args = _build_parser().parse_args()

    job = OptionsVolJob(
        symbol=args.symbol,
        save_symbol=args.save_symbol,
        target_delta=args.delta,
        rv_window=args.rv_window,
    )

    if args.first_time:
        job.authenticate()
        sys.exit(0)

    if args.backfill:
        job.backfill()
        sys.exit(0)

    results = job.run()
    print("\n--- Summary ---")
    for name, df in results.items():
        print(f"  {name:20s}: {len(df)} rows")
