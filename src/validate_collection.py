"""
src/validate_collection.py

Answers the question pipeline-heartbeat.yml deliberately doesn't: was data
actually *collected*, for every symbol, or did the run merely happen?

Why this exists
---------------
pipeline-heartbeat.yml checks that daily-snapshot.yml fired at all, which
catches GitHub dropping a scheduled cron. It does not look at what the run
produced, and says so in its own header. That left a real blind spot, and
it cost two weeks of data: from 2026-08-28 to 2026-09-11 the workflow ran
every single weekday and failed every single time. A run existed each day,
so the heartbeat passed for twelve consecutive days while nothing whatever
was being collected. Nobody found out until the history was inspected by
hand.

This closes that gap by reading the committed history rather than the run
status: every symbol in SYMBOL_REGISTRY must have recorded metrics on a
recent collection day, or this exits non-zero and names the ones that
didn't.

On market holidays
------------------
There's no maintained market-holiday calendar in this project (the same
limitation data_quality.py documents), so a holiday is indistinguishable
from a missed day here. Rather than guess, this tolerates a few quiet days
before failing -- see --max-stale-days. A single holiday, or a holiday
adjacent to a weekend, passes quietly; a genuine outage still surfaces
within a few days instead of a fortnight. That tradeoff is deliberate: an
alert that cries wolf on every Thanksgiving gets muted, and a muted alert
is how this problem happened in the first place.

Usage:
    python -m src.validate_collection                    # defaults
    python -m src.validate_collection --max-stale-days 5
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from src.dashboard import data_trust
from src.history_store import HistoryStore
from src.symbols import SYMBOL_REGISTRY


def _annotate(level: str, message: str) -> None:
    """Emit a GitHub Actions annotation when running in CI, plain text
    otherwise -- ::error:: lines surface on the run's summary page instead of
    being buried in the log, which is the difference between a failure
    somebody sees and one they don't."""
    print(f"::{level}::{message}" if _in_ci() else f"[{level.upper()}] {message}")


def _in_ci() -> bool:
    import os

    return os.environ.get("GITHUB_ACTIONS") == "true"


def validate(max_stale_days: int = 3, window_trading_days: int = 45, today: date | None = None) -> int:
    """Returns a process exit code: 0 healthy, 1 problems found."""
    today = today or date.today()
    store = HistoryStore()

    coverage = {
        symbol: (meta.color, store.snapshot_dates(symbol))
        for symbol, meta in SYMBOL_REGISTRY.items()
    }
    report = data_trust.build_trust_report(coverage, today=today, window_trading_days=window_trading_days)

    latest = report["latest_complete_run"]
    since = report["trading_days_since_complete_run"]
    print(f"Tracked symbols:        {report['symbol_count']}")
    print(f"Latest complete run:    {latest or 'never'}")
    print(f"Collection days since:  {since if since is not None else 'n/a'}")
    print()

    problems = []

    if latest is None:
        problems.append(
            f"No complete pipeline run in the last {len(report['calendar'])} collection days. "
            "Nothing is being collected at all."
        )
    elif since is not None and since > max_stale_days:
        problems.append(
            f"Last complete run was {latest} -- {since} collection days ago, over the "
            f"{max_stale_days}-day threshold. Data collection has stopped."
        )

    # Per-symbol gaps: one symbol silently dropping out while the other 23
    # keep working won't move the "complete run" figure above, but it still
    # quietly poisons that symbol's IV Rank and VRP.
    stale = [
        r for r in report["rows"]
        if r["age_trading_days"] is None or r["age_trading_days"] > max_stale_days
    ]
    for row in sorted(stale, key=lambda r: r["symbol"]):
        last = row["last_snapshot_date"] or "never"
        age = "never recorded" if row["age_trading_days"] is None else f"{row['age_trading_days']} days ago"
        problems.append(f"{row['symbol']}: last recorded {last} ({age}).")

    if problems:
        for p in problems:
            _annotate("error", p)
        print()
        _annotate(
            "error",
            "Data collection validation FAILED. Check which step of daily-snapshot.yml failed "
            "before assuming it's the Schwab token -- see "
            ".claude/skills/vol-dashboard-development/references/known-issues.md",
        )
        return 1

    print(f"OK -- all {report['symbol_count']} symbols recorded data within {max_stale_days} collection days.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-stale-days",
        type=int,
        default=3,
        help="Collection days a symbol may go without data before this fails (default: 3, "
        "chosen to absorb a market holiday without crying wolf).",
    )
    parser.add_argument("--window", type=int, default=45, help="Coverage window in collection days (default: 45).")
    args = parser.parse_args()
    return validate(max_stale_days=args.max_stale_days, window_trading_days=args.window)


if __name__ == "__main__":
    sys.exit(main())
