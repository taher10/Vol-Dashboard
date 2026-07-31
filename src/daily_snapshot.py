"""
src/daily_snapshot.py

Headless multi-symbol runner: fetches every symbol in SYMBOL_REGISTRY via
OptionsVolJob (same fetch -> save -> compute path job.py uses for one
symbol) and appends each result into HistoryStore (history/vol_history.db)
so history actually accumulates day over day.

This is what .github/workflows/daily-snapshot.yml runs on a schedule --
the workflow commits history/vol_history.db afterward so it survives
between runs (git-hosted persistence, not a hosted database -- see
src/history_store.py's docstring for why).

One symbol failing (auth hiccup, Schwab rate limit, a bad expiry) does not
abort the run -- every other symbol still gets fetched, and the script exits
non-zero only if every symbol hit a genuine error, so a CI schedule doesn't
need special handling for "one name had a bad day."

Deliberately does NOT exit non-zero when every symbol is merely "skipped"
(LiveDataUnavailableError, e.g. every name legitimately has no quotes on a
market holiday that still falls on a cron weekday) -- that's a correct,
expected outcome some days, not a bug, and treating it as a CI failure would
train whoever's watching to ignore real failure alerts. Instead this always
writes a clear ok/skipped/failed breakdown to $GITHUB_STEP_SUMMARY (visible
directly on the run's summary page, no log access needed) so a *pattern* of
all-skipped days -- as opposed to one holiday -- is easy to notice. Catching
that pattern automatically (not just leaving it visible) is what the
separate heartbeat workflow is for (.github/workflows/pipeline-heartbeat.yml).

Usage
-----
    python -m src.daily_snapshot
    python -m src.daily_snapshot --symbols SPX AAPL   # subset, e.g. for testing
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.data_quality import LiveDataUnavailableError
from src.history_store import HistoryStore
from src.job import OptionsVolJob
from src.notify import notify
from src.symbols import SYMBOL_REGISTRY

# Errors matching this are never worth retrying (see _is_auth_error) -- a
# dead refresh token stays dead until someone completes the OAuth browser
# flow again, so retrying just wastes the retry budget and delays surfacing
# the real problem.
_AUTH_ERROR_MARKERS = ("invalid_grant", "Refresh token")

# How many extra attempts a symbol gets after a transient failure (network
# blip, momentary Schwab 5xx/429) before it's recorded as failed for today.
_MAX_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 5.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("daily_snapshot")


def _is_auth_error(exc: Exception) -> bool:
    """True for a dead/expired/revoked refresh token -- see src/token_sync.py
    for the fix (auto-persisting a refreshed token back to the GitHub
    secret). Retrying this is pointless: it fails identically every time
    until someone reruns the OAuth browser flow."""
    text = str(exc)
    return any(marker in text for marker in _AUTH_ERROR_MARKERS)


def _fetch_symbol(symbol: str, info, store: HistoryStore, snapshot_date) -> str:
    """Run one symbol with a couple of retries for transient errors (network
    blips, momentary Schwab 5xx/429). Returns the same status-string contract
    `run()` has always used: 'ok' | 'skipped (...)' | an error message."""
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 2):
        try:
            job = OptionsVolJob(
                symbol=info.api_symbol,
                save_symbol=symbol,
                strike_increment=info.strike_increment,
                strikes_each_side=info.strikes_each_side,
            )
            metrics = job.run()
            n_rows = store.append_snapshot(symbol, snapshot_date, metrics)
            logger.info("%s: appended %d expiration rows to history", symbol, n_rows)
            return "ok"
        except LiveDataUnavailableError as exc:
            # Expected/benign, not a real failure: job.py already declined to
            # overwrite the last good snapshot with this one, so there's
            # nothing new to append to history today, but nothing was lost either.
            logger.info("%s: live data unavailable, skipping today's history append", symbol)
            return f"skipped (live data unavailable): {exc}"
        except Exception as exc:
            last_exc = exc
            if _is_auth_error(exc) or attempt > _MAX_RETRIES:
                logger.exception("%s: failed", symbol)
                break
            logger.warning(
                "%s: attempt %d/%d failed (%s), retrying in %.0fs ...",
                symbol, attempt, _MAX_RETRIES + 1, exc, _RETRY_BACKOFF_SECONDS,
            )
            time.sleep(_RETRY_BACKOFF_SECONDS)

    return str(last_exc)


def run(symbols: list[str]) -> dict[str, str]:
    """Fetch + append-to-history every symbol in `symbols`. Returns {symbol: 'ok'|error message}."""
    store = HistoryStore()
    results: dict[str, str] = {}
    snapshot_date = datetime.now(UTC).date()

    for symbol in symbols:
        info = SYMBOL_REGISTRY.get(symbol)
        if info is None:
            results[symbol] = f"not in SYMBOL_REGISTRY"
            logger.error("%s: not in SYMBOL_REGISTRY, skipping", symbol)
            continue

        results[symbol] = _fetch_symbol(symbol, info, store, snapshot_date)

    _maybe_notify(results)
    return results


def _maybe_notify(results: dict[str, str]) -> None:
    """Send a webhook notification (see src/notify.py) when there's something
    a human should actually look at. Silent on an all-ok or all-benign-skip
    day (a market holiday skips every symbol -- that's expected, not a page-
    worthy event) so the notification channel stays meaningful instead of
    turning into daily noise that gets ignored."""
    failed = {s: msg for s, msg in results.items() if msg != "ok" and not msg.startswith("skipped")}
    if not failed:
        return

    reauth_needed = any(_is_auth_error(RuntimeError(msg)) for msg in failed.values())
    lines = ["Vol Dashboard daily snapshot: %d symbol(s) failed" % len(failed)]
    if reauth_needed:
        lines.append(
            "⚠️ Refresh token expired/invalid -- Schwab needs a fresh manual "
            "OAuth login (`python -m src.job --first-time`), then re-upload "
            "SCHWAB_TOKEN_B64. See src/token_sync.py for the auto-refresh fix "
            "that should prevent this going forward."
        )
    for symbol, msg in sorted(failed.items()):
        lines.append(f"- {symbol}: {msg}")
    notify("\n".join(lines))


def _write_step_summary(results: dict[str, str]) -> None:
    """
    Write an ok/skipped/failed breakdown to $GITHUB_STEP_SUMMARY (a GitHub
    Actions feature: markdown written here renders directly on the run's
    summary page, visible to anyone who can see the Actions tab -- unlike raw
    step logs, which need repo-admin auth to fetch via the API). No-op
    outside GitHub Actions (env var unset), so this is harmless to call
    locally/in tests.
    """
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return

    ok = sorted(s for s, status in results.items() if status == "ok")
    skipped = sorted(s for s, status in results.items() if status.startswith("skipped"))
    failed = sorted(s for s in results if s not in ok and s not in skipped)

    lines = [
        "## Daily snapshot summary",
        f"- ✅ **{len(ok)} ok**: {', '.join(ok) if ok else '(none)'}",
        f"- ⏭️ **{len(skipped)} skipped** (no live quotes -- expected on a market holiday): {', '.join(skipped) if skipped else '(none)'}",
        f"- ❌ **{len(failed)} failed**: {', '.join(failed) if failed else '(none)'}",
    ]
    if not ok and not failed:
        lines.append(
            "\n⚠️ **Every symbol was skipped today.** Normal on a market holiday; "
            "worth a manual look if this repeats on a normal trading day."
        )
    if failed:
        lines.append("\n**Failure detail:**")
        for s in failed:
            lines.append(f"- `{s}`: {results[s]}")

    with open(path, "a") as f:
        f.write("\n".join(lines) + "\n")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fetch every registered symbol and append to the history DB.")
    p.add_argument(
        "--symbols", nargs="+", default=list(SYMBOL_REGISTRY.keys()),
        help="Subset of symbols to run (default: every symbol in SYMBOL_REGISTRY)",
    )
    return p


if __name__ == "__main__":
    args = _build_parser().parse_args()
    results = run(args.symbols)

    print("\n--- Daily snapshot summary ---")
    for symbol, status in results.items():
        print(f"  {symbol:6s}: {status}")

    _write_step_summary(results)

    # "skipped (live data unavailable)" is expected/benign (see LiveDataUnavailableError
    # handling above), not a failure -- only exit non-zero if every symbol hit a real error.
    if all(status != "ok" and not status.startswith("skipped") for status in results.values()):
        sys.exit(1)
