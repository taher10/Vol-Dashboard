"""
src/dashboard/earnings.py

The hand-maintained earnings calendar, and what it is for.

Why this exists
---------------
Trade Ideas ranks by richness, and that ranking is dominated by names with
earnings coming. It has to be: implied vol rises before a binary event
because the event is real, so a pre-earnings name looks rich by
construction. The practical result was eight cash-secured puts on eight
tickers that were all the same trade -- being paid to carry event risk,
which is not an edge.

Knowing the dates turns that from a hidden bias into a visible label.

Why by hand
-----------
Four dates a year across 24 symbols is a few minutes a quarter. The
alternatives were a scraped Yahoo endpoint -- unofficial, breaks without
warning, and a terms-of-service grey area this project already declined
once for Google Finance -- or a third-party API key for data that barely
changes. A wrong date mislabels which expiry carries the event, so accuracy
matters more than convenience here.

What this deliberately does NOT do
----------------------------------
It does not judge whether the implied move is too high. Doing that needs
historical earnings moves, and the price history holds roughly four events
per symbol (454 days as of 2026-10). A median of four observations is an
anecdote with a decimal point, not a statistic, and dressing it up as an
edge is exactly the kind of fabricated precision the rest of this project
refuses. Showing the four moves individually, labelled with n, is honest;
averaging them into a verdict is not.
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent.parent
CALENDAR_PATH = _PROJECT_ROOT / "data" / "earnings_dates.json"


@lru_cache(maxsize=4)
def _load(path_str: str, _mtime: float) -> dict[str, list[date]]:
    """Parsed calendar, cached against the file's mtime so an edit is picked
    up without a restart."""
    try:
        raw = json.loads(Path(path_str).read_text())
    except (OSError, json.JSONDecodeError):
        return {}

    parsed: dict[str, list[date]] = {}
    for symbol, dates in (raw.get("earnings") or {}).items():
        good = []
        for value in dates:
            try:
                good.append(date.fromisoformat(value))
            except (TypeError, ValueError):
                continue  # a typo in one row must not blank the whole calendar
        parsed[symbol.upper()] = sorted(good)
    return parsed


def calendar(path: Path | None = None) -> dict[str, list[date]]:
    target = path or CALENDAR_PATH
    try:
        mtime = target.stat().st_mtime
    except OSError:
        mtime = 0.0
    return _load(str(target), mtime)


def next_earnings(symbol: str, today: date, path: Path | None = None) -> date | None:
    """The next announcement on or after `today`, or None.

    None means "no date on file", which is NOT the same as "no earnings
    coming" -- the calendar is hand-kept and may simply be out of date.
    Callers must present it as unknown rather than as clear.
    """
    for announced in calendar(path).get(symbol.upper(), []):
        if announced >= today:
            return announced
    return None


def past_earnings(symbol: str, today: date, limit: int = 8, path: Path | None = None) -> list[date]:
    """Past announcements, most recent first -- the basis for a historical
    move comparison, whenever there is enough history to support one."""
    past = [d for d in calendar(path).get(symbol.upper(), []) if d < today]
    return sorted(past, reverse=True)[:limit]


def has_calendar(symbol: str, path: Path | None = None) -> bool:
    """Whether any dates are on file for this symbol, so the UI can say
    'no earnings date on file' rather than implying none are due."""
    return bool(calendar(path).get(symbol.upper()))
