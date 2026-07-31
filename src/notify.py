"""
src/notify.py

Optional webhook notification helper for pipeline problems (auth failures,
per-symbol errors). Sends a single JSON POST compatible with both Slack
incoming webhooks (which read the "text" key) and Discord webhooks (which
read "content") -- both keys are sent in every payload so the same
NOTIFY_WEBHOOK_URL secret works with either without needing to know which
service it points at; each ignores the key it doesn't recognize.

No-op if NOTIFY_WEBHOOK_URL isn't set, so this is always safe to call --
locally, in tests, or from any environment that hasn't configured it.

Usage:
    from src.notify import notify
    notify("SPX: failed -- invalid_grant (refresh token expired, re-auth needed)")
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger("notify")


def notify(message: str) -> bool:
    """POST `message` to NOTIFY_WEBHOOK_URL if configured. Returns True if it was sent."""
    url = os.environ.get("NOTIFY_WEBHOOK_URL")
    if not url:
        return False
    try:
        resp = httpx.post(url, json={"text": message, "content": message}, timeout=15.0)
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("Failed to send notification: %s", exc)
        return False
