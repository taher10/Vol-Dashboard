"""
src/charts.py

Generate charts from the latest saved metrics CSV files.

Outputs (PNG) are written to:
    charts/

Charts generated:
    - term_structure.png
    - skew.png
    - curvature.png
    - vrp.png (if available)

Usage:
    python -m src.charts
    python src/charts.py --symbol SPX --show
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, UTC
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import matplotlib.pyplot as plt
import pandas as pd

from src.data_store import CSVStore


class MetricsChartBuilder:
    """Builds and saves charts from latest metrics snapshots."""

    def __init__(self, symbol: str = "SPX", charts_dir: Path | None = None) -> None:
        self.symbol = symbol
        self.store = CSVStore(symbol=symbol)
        self.charts_dir = charts_dir or (_PROJECT_ROOT / "charts")
        self.charts_dir.mkdir(parents=True, exist_ok=True)

    def build_all(self, show: bool = False) -> list[Path]:
        saved: list[Path] = []

        term = self.store.load_latest_metrics("term_structure")
        skew = self.store.load_latest_metrics("skew")
        curv = self.store.load_latest_metrics("curvature")

        saved.append(self._plot_term_structure(term))
        saved.append(self._plot_skew(skew))
        saved.append(self._plot_curvature(curv))

        try:
            vrp = self.store.load_latest_metrics("vrp")
            saved.append(self._plot_vrp(vrp))
        except FileNotFoundError:
            pass

        if show:
            plt.show()
        else:
            plt.close("all")

        return saved

    def _plot_term_structure(self, df: pd.DataFrame) -> Path:
        fig, ax = plt.subplots(figsize=(9, 5))
        ax.plot(df["dte"], df["atm_iv"], marker="o", linewidth=2)
        ax.set_title(f"{self.symbol} ATM IV Term Structure")
        ax.set_xlabel("DTE")
        ax.set_ylabel("ATM IV (%)")
        ax.grid(True, alpha=0.3)
        path = self.charts_dir / f"{self.symbol}_term_structure.png"
        fig.tight_layout()
        fig.savefig(path, dpi=140)
        return path

    def _plot_skew(self, df: pd.DataFrame) -> Path:
        fig, ax = plt.subplots(figsize=(9, 5))
        ax.plot(df["dte"], df["skew"], marker="o", linewidth=2, color="tab:red")
        ax.axhline(0.0, color="black", linewidth=1, alpha=0.6)
        ax.set_title(f"{self.symbol} 25D Put-Call Skew")
        ax.set_xlabel("DTE")
        ax.set_ylabel("Skew (IV points)")
        ax.grid(True, alpha=0.3)
        path = self.charts_dir / f"{self.symbol}_skew.png"
        fig.tight_layout()
        fig.savefig(path, dpi=140)
        return path

    def _plot_curvature(self, df: pd.DataFrame) -> Path:
        fig, ax = plt.subplots(figsize=(9, 5))
        ax.plot(df["dte"], df["curvature"], marker="o", linewidth=2, color="tab:purple")
        ax.axhline(0.0, color="black", linewidth=1, alpha=0.6)
        ax.set_title(f"{self.symbol} Smile Curvature")
        ax.set_xlabel("DTE")
        ax.set_ylabel("Curvature (IV points)")
        ax.grid(True, alpha=0.3)
        path = self.charts_dir / f"{self.symbol}_curvature.png"
        fig.tight_layout()
        fig.savefig(path, dpi=140)
        return path

    def _plot_vrp(self, df: pd.DataFrame) -> Path:
        fig, ax = plt.subplots(figsize=(9, 5))
        ax.plot(df["dte"], df["vrp"], marker="o", linewidth=2, color="tab:green")
        ax.axhline(0.0, color="black", linewidth=1, alpha=0.6)
        ax.set_title(f"{self.symbol} Volatility Risk Premium")
        ax.set_xlabel("DTE")
        ax.set_ylabel("VRP (IV - RV)")
        ax.grid(True, alpha=0.3)
        path = self.charts_dir / f"{self.symbol}_vrp.png"
        fig.tight_layout()
        fig.savefig(path, dpi=140)
        return path


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Generate charts from latest metrics CSV files.")
    p.add_argument("--symbol", default="SPX", help="Data symbol label used in filenames.")
    p.add_argument("--show", action="store_true", help="Display plots interactively.")
    return p


if __name__ == "__main__":
    args = _build_parser().parse_args()
    builder = MetricsChartBuilder(symbol=args.symbol)
    saved_paths = builder.build_all(show=args.show)

    print(f"Generated {len(saved_paths)} chart(s) at {datetime.now(UTC).isoformat(timespec='seconds')}")
    for p in saved_paths:
        print(f"  - {p}")
