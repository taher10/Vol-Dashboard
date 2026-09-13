"""
src/preflight_check.py

A cheap mid-session smoke test that the Schwab credentials still work, run
hours before the day's real collection window.

Why this exists
---------------
daily-snapshot.yml runs at 21:30 UTC, after the close. If the token is dead
by then, that day's data is already unrecoverable -- Schwab serves current
chains only, so a missed session is a permanent hole in the history, not
something a rerun can fill. Every failure mode this project has hit (an
expired refresh token, an undecodable SCHWAB_TOKEN_B64, a dependency that
broke the HTTP stack) would have been visible hours earlier from a single
authenticated request.

So this authenticates and pulls one expiry for one symbol during market
hours. If it fails there is time to fix it before the collection window;
if it passes, the evening run is very likely to work.

The token-rotation trap
-----------------------
Schwab rotates the refresh token on every use, and the GitHub runner's disk
is ephemeral. So a workflow that authenticates and then throws the refreshed
token away leaves SCHWAB_TOKEN_B64 holding a token Schwab has already
invalidated -- the evening run would then fail with invalid_grant, caused by
the very check meant to protect it. Any workflow calling this MUST also run
`python -m src.token_sync` afterwards, exactly as daily-snapshot.yml does.
That is not optional and it is why this module does not sync the token
itself: keeping the sync as a visible, separate workflow step makes it much
harder to drop when someone edits the YAML later.

Deliberately cheap: one expiry, few strikes -- about 1 API call against a
768-call daily budget, versus the ~8.5 minutes a full run costs.

Usage:
    python -m src.preflight_check
    python -m src.preflight_check --symbol AAPL
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta

import schwab

from src.auth import SchwabAuth
from src.data_quality import is_chain_usable, is_regular_market_hours
from src.options_fetcher import OptionsFetcher, expiry_dates_for_pull


def _annotate(level: str, message: str) -> None:
    print(f"::{level}::{message}" if os.environ.get("GITHUB_ACTIONS") == "true" else f"[{level.upper()}] {message}")


def preflight(symbol: str = "AAPL", strike_count: int = 6) -> int:
    """Returns a process exit code: 0 credentials work, 1 they don't."""
    try:
        auth = SchwabAuth.from_env()
        client = auth.get_client()
    except Exception as exc:  # noqa: BLE001 -- any failure here is the answer
        _annotate("error", f"Schwab authentication failed: {type(exc).__name__}: {exc}")
        _annotate(
            "error",
            "The evening collection run will fail unless this is fixed. See "
            ".claude/skills/vol-dashboard-development/references/known-issues.md",
        )
        return 1

    upcoming = expiry_dates_for_pull(date.today(), date.today() + timedelta(days=45))
    if not upcoming:
        _annotate("error", "No upcoming expiries resolved -- expiry date logic returned nothing.")
        return 1

    fetcher = OptionsFetcher(client, symbol=symbol)
    try:
        chain = fetcher._fetch_single_expiry(
            schwab.client.Client.Options.ContractType.CALL, upcoming[0], strike_count
        )
    except Exception as exc:  # noqa: BLE001
        _annotate("error", f"Authenticated but the chain request failed for {symbol}: {type(exc).__name__}: {exc}")
        return 1

    if chain is None or chain.empty:
        _annotate("error", f"Authenticated but Schwab returned an empty chain for {symbol} {upcoming[0]}.")
        return 1

    # A chain full of -999 sentinels parses fine and isn't empty, so check the
    # quotes are real rather than just counting rows. Outside regular hours
    # that's expected and not a credential problem, which is the one thing
    # this check exists to distinguish -- so don't fail the run for it.
    if not is_chain_usable(chain):
        if is_regular_market_hours():
            _annotate(
                "error",
                f"{symbol} {upcoming[0]}: authenticated, but quotes are unusable during regular "
                "market hours -- Schwab is returning -999 sentinels when it shouldn't be.",
            )
            return 1
        _annotate(
            "warning",
            f"{symbol} {upcoming[0]}: quotes unusable, but it's outside regular market hours -- "
            "expected, and not a credential problem. Credentials verified OK.",
        )
        return 0

    print(f"OK -- authenticated and fetched {len(chain)} usable contracts for {symbol} {upcoming[0]}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="AAPL", help="Symbol to smoke-test with (default: AAPL).")
    parser.add_argument("--strike-count", type=int, default=6, help="Strikes around ATM (default: 6).")
    args = parser.parse_args()
    return preflight(symbol=args.symbol, strike_count=args.strike_count)


if __name__ == "__main__":
    sys.exit(main())
