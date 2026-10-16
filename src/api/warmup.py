"""
src/api/warmup.py

Fills the row caches in the background at startup, so the first visitor
doesn't pay for them.

Why
---
Caching the leaderboard rows took a warm request from ~9.5s to ~0.15s on
the public demo, but the FIRST request after the process starts still does
the real work -- loading 24 chain frames, z-score lookups, scoring -- which
is ~18s for the Vol Scanner on a free-tier CPU roughly 30x slower than a
laptop. The instance sleeps after 15 minutes of inactivity, so the process
is new, the caches are empty, and someone pays that again.

Render is already spending ~50 seconds waking the container. Warming in
parallel with that costs the visitor nothing and usually finishes first.

Deliberately a background thread, not a blocking startup step: Render's
health check has to pass promptly or the deploy is marked failed, and a
slow warm must never be the reason a deployment is rejected. Requests
served during warming are correct, just not yet fast -- they compute and
populate the same cache.

Off unless WARM_CACHE is set. Local development has nothing to gain (cold
is already sub-second there), the test suite must not spawn threads that
touch the committed database, and a feature that only matters in one
deployment should be switched on in that deployment rather than everywhere.
"""

from __future__ import annotations

import logging
import os
import threading
import time

# Logs under uvicorn's namespace so the messages actually appear in the
# server output. uvicorn configures its own loggers and leaves the root
# logger without a handler, so getLogger("warmup") would be silent in the
# one deployment this module exists for.
logger = logging.getLogger("uvicorn.error")

_TRUTHY = {"1", "true", "yes", "on"}

# These mirror the Query() defaults on the endpoints in routes.py, and have
# to stay in step with them: the cache is keyed on arguments, so warming
# (30, 0.30) when the page asks for (30, 0.50) populates an entry nobody
# wants and leaves the real one cold. That exact mismatch is why
# /scanner/delta-neutral stayed at 0.42s through the first version of this.
# test_warmup.py asserts these against the live signatures.
_DEFAULT_TARGET_DTE = 30            # scanner, gamma-exposure, delta-neutral
_DEFAULT_CALENDAR = (7, 30, 0.25)   # calendar-edge: front, back, delta
_DEFAULT_TERM_STRUCTURE = (7, 60)   # term-structure: near, far
_DEFAULT_DELTA = 0.50               # delta-neutral


def enabled() -> bool:
    return os.environ.get("WARM_CACHE", "").strip().lower() in _TRUTHY


def warm() -> None:
    """Populate every row cache for the default parameters.

    Each symbol is wrapped individually: one symbol with a thin chain must
    not abandon the other 23. Failures are logged and skipped, because a
    warm failure is a performance problem, never a correctness one -- the
    request path will compute that row itself later.
    """
    from src.api import routes
    from src.symbols import SYMBOL_REGISTRY

    started = time.monotonic()
    warmed = failed = 0

    for symbol in SYMBOL_REGISTRY:
        for call in (
            lambda s=symbol: routes._scanner_row(s, _DEFAULT_TARGET_DTE),
            lambda s=symbol: routes._calendar_edge_row(s, *_DEFAULT_CALENDAR),
            lambda s=symbol: routes._term_structure_row(s, *_DEFAULT_TERM_STRUCTURE),
            lambda s=symbol: routes._delta_neutral_row(s, _DEFAULT_TARGET_DTE, _DEFAULT_DELTA),
            lambda s=symbol: routes._gamma_exposure_row(s, _DEFAULT_TARGET_DTE),
            lambda s=symbol: routes._trade_idea(s),
        ):
            try:
                call()
                warmed += 1
            except Exception:  # noqa: BLE001 -- warming must never take the app down
                failed += 1
                logger.debug("warmup failed for %s", symbol, exc_info=True)

    try:
        routes._pcr_snapshot(None)
        warmed += 1
    except Exception:  # noqa: BLE001
        failed += 1
        logger.debug("warmup failed for pcr", exc_info=True)

    logger.info(
        "cache warm complete: %d entries in %.1fs%s",
        warmed, time.monotonic() - started, f", {failed} skipped" if failed else "",
    )


def start_background_warm() -> threading.Thread | None:
    """Kick off warm() off the main thread. Returns None when disabled."""
    if not enabled():
        return None
    thread = threading.Thread(target=warm, name="cache-warmup", daemon=True)
    thread.start()
    logger.info("cache warm started in the background")
    return thread
