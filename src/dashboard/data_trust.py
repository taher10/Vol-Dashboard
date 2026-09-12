"""
src/dashboard/data_trust.py

Answers one question the rest of the dashboard never asks: how much of what
you're looking at is actually backed by data?

Why this exists: every page here renders numbers from the latest stored
snapshot with no indication of when that snapshot is from, and every
history-derived figure (IV Rank, IV percentile, z-scores) is computed over
however many observations happen to exist -- HistoryStore.iv_rank() will
return a percentile off 2 stored dates while advertising a 365-day lookback.
A rank computed from 3 observations and one computed from 300 render
identically today, so "I don't really know" is indistinguishable from a real
signal. This module computes the coverage facts needed to tell them apart,
and deliberately reports raw counts (n, gap lengths, dates) rather than a
confidence score, so nothing here invents certainty the data doesn't have.

Trading days here means weekdays. There's no maintained market-holiday
calendar in this project (the same limitation data_quality.py documents for
is_regular_market_hours), so a real holiday reads as a missing day. That's
surfaced in the UI copy rather than silently corrected -- a false gap the
user can explain is safer than a real gap quietly papered over.
"""

from __future__ import annotations

from datetime import date, timedelta

# A date is "complete" when at least this fraction of tracked symbols recorded
# metrics for it -- the daily job writes every symbol in one run, so a date
# with only a handful of symbols is a partial/manual run, not a pipeline day.
_COMPLETE_RUN_FRACTION = 0.9


def trading_days(start: date, end: date) -> list[date]:
    """Weekdays from `start` to `end` inclusive, ascending. Holidays are not
    excluded -- see the module docstring for why that's deliberate."""
    days = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _largest_missing_run(calendar: list[date], covered: set[date]) -> int:
    """Longest run of consecutive expected trading days with no data."""
    longest = current = 0
    for day in calendar:
        current = 0 if day in covered else current + 1
        longest = max(longest, current)
    return longest


def _symbol_row(symbol: str, color: str, dates: list[date], calendar: list[date]) -> dict:
    """Coverage facts for one symbol. `dates` is every date this symbol has a
    stored metrics row for (all time); `calendar` is the display window."""
    covered = set(dates)
    in_window = [d for d in calendar if d in covered]
    last_date = dates[-1] if dates else None

    return {
        "symbol": symbol,
        "color": color,
        "last_snapshot_date": last_date.isoformat() if last_date else None,
        # Counted as collection days elapsed within the displayed window rather
        # than raw calendar days, so a Friday snapshot read on Monday is 1 day
        # old and not 3. Measured off `calendar` itself instead of recomputing
        # weekdays: the calendar already includes any off-schedule day that
        # actually produced data (a manually triggered weekend run), and a day
        # the pipeline really ran on must count as a day it could have run on.
        # For a symbol whose last data predates the window this is a floor, not
        # the true age -- last_snapshot_date is shown alongside so the real date
        # is never hidden behind the derived number.
        "age_trading_days": sum(1 for d in calendar if d > last_date) if last_date else None,
        "n_observations": len(dates),
        "days_covered_in_window": len(in_window),
        "largest_gap_trading_days": _largest_missing_run(calendar, covered),
        "covered_days": [d.isoformat() for d in in_window],
        # The real, derived consequence of a small sample: with n observations
        # a percentile can only land on n distinct values, so it resolves to
        # no finer than this many points. Reported instead of a made-up
        # confidence score -- it's a fact about the data, not a judgement.
        "percentile_resolution_pts": round(100.0 / len(dates), 1) if dates else None,
    }


def build_trust_report(
    coverage_by_symbol: dict[str, tuple[str, list[date]]],
    today: date,
    window_trading_days: int = 45,
) -> dict:
    """
    Cross-symbol pipeline coverage over the trailing `window_trading_days`.

    `coverage_by_symbol` maps symbol -> (display color, every stored snapshot
    date for that symbol, ascending).

    Returns the display window's calendar, one row per symbol, and the
    headline pipeline facts -- the most recent date on which the pipeline
    recorded a full run, and how long ago that was. A symbol with no stored
    history at all still gets a row (with nulls and a zero count) rather than
    being dropped: "this symbol has never recorded data" is exactly the kind
    of gap this page exists to make visible.
    """
    # Walk back far enough in calendar days to be sure of covering the
    # requested number of weekdays, then keep the trailing window.
    weekdays = trading_days(today - timedelta(days=window_trading_days * 2 + 10), today)[-window_trading_days:]

    # Any day that actually produced data belongs in the window even if it
    # isn't a weekday. The pipeline's cron is Mon-Fri, but a manually
    # triggered run lands on whatever day it's triggered -- and a weekend
    # recovery run was invisible here until it was added, which made a fully
    # recovered pipeline still report its last complete run as weeks earlier.
    # Only days with data are added, never bare weekend days, so this can't
    # manufacture a gap that the schedule never intended to fill.
    window_start = weekdays[0] if weekdays else today
    off_schedule = {
        day
        for _, dates in coverage_by_symbol.values()
        for day in dates
        if day.weekday() >= 5 and window_start <= day <= today
    }
    calendar = sorted(set(weekdays) | off_schedule)

    rows = [_symbol_row(symbol, color, dates, calendar) for symbol, (color, dates) in coverage_by_symbol.items()]

    symbol_count = len(coverage_by_symbol)
    needed_for_complete = max(1, int(symbol_count * _COMPLETE_RUN_FRACTION))
    per_day_counts = {
        day: sum(1 for _, dates in coverage_by_symbol.values() if day in set(dates)) for day in calendar
    }
    complete_days = [day for day, count in per_day_counts.items() if count >= needed_for_complete]
    latest_complete = max(complete_days) if complete_days else None

    return {
        "today": today.isoformat(),
        "calendar": [d.isoformat() for d in calendar],
        "symbol_count": symbol_count,
        "symbols_reporting_per_day": {d.isoformat(): per_day_counts[d] for d in calendar},
        "complete_run_threshold": needed_for_complete,
        "latest_complete_run": latest_complete.isoformat() if latest_complete else None,
        "trading_days_since_complete_run": (
            sum(1 for d in calendar if d > latest_complete) if latest_complete else None
        ),
        "rows": rows,
    }
