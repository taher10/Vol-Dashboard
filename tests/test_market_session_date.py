"""
Tests for data_quality.market_session_date.

Snapshots were dated `datetime.now(UTC).date()`. The cron fires at 21:30
UTC -- 17:30 ET, after the close -- but GitHub's queue has been delaying
runs two to three hours, and 21:30 + 3h crosses midnight UTC while it is
still only 20:30 the same evening in New York. Wednesday's closing quotes
were filed under Thursday.

Three of four collections in mid-October 2026 were a day late, and one
earlier run produced a 2026-09-26 snapshot -- a Saturday, on which no
options session exists. IV Rank, the trailing z-scores, VRP and the
backtest all index on snapshot_date, so every one of those comparisons was
misaligned by a day against the real market.

The cases below are the actual timestamps from those runs, not invented
ones.
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from src.data_quality import market_session_date

ET = ZoneInfo("America/New_York")


def _utc(y, m, d, hh, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=UTC)


class TestTheBugItFixes:
    def test_run_delayed_past_midnight_utc(self):
        """The real failure: started 2026-10-15 00:30 UTC, which is 20:30 ET
        on Wednesday the 14th. Previously dated 10-15."""
        assert market_session_date(_utc(2026, 10, 15, 0, 30)) == date(2026, 10, 14)

    def test_on_time_run_is_unchanged(self):
        """21:30 UTC = 17:30 ET the same day. The fix must not move the
        dates that were already right."""
        assert market_session_date(_utc(2026, 10, 14, 21, 30)) == date(2026, 10, 14)

    def test_never_produces_a_weekend_session(self):
        """A run just after midnight UTC on Saturday produced a 2026-09-26
        label. There is no Saturday options session; those quotes are
        Friday's close."""
        session = market_session_date(_utc(2026, 9, 26, 0, 0))

        assert session == date(2026, 9, 25)
        assert session.weekday() < 5

    def test_three_hour_delay_still_lands_right(self):
        """The margin that matters: ET does not roll over until 04:00 UTC,
        so any plausible queue delay stays on the correct session."""
        for extra_hours in range(0, 6):
            fired = _utc(2026, 10, 14, 21, 30)
            delayed = fired.replace(hour=(21 + extra_hours) % 24,
                                    day=14 + (21 + extra_hours) // 24)
            assert market_session_date(delayed) == date(2026, 10, 14), extra_hours


class TestSessionBoundaries:
    def test_after_the_close_is_todays_session(self):
        assert market_session_date(_utc(2026, 10, 14, 20, 30)) == date(2026, 10, 14)

    def test_during_the_session_is_today(self):
        assert market_session_date(_utc(2026, 10, 14, 17, 0)) == date(2026, 10, 14)

    def test_before_the_open_is_the_previous_session(self):
        """08:00 ET -- the session has not happened, so the freshest quotes
        are yesterday's close."""
        assert market_session_date(_utc(2026, 10, 15, 12, 0)) == date(2026, 10, 14)

    def test_monday_before_open_walks_back_to_friday(self):
        monday_pre_open = _utc(2026, 10, 19, 12, 0)  # 08:00 ET Monday

        session = market_session_date(monday_pre_open)

        assert session == date(2026, 10, 16)
        assert session.strftime("%a") == "Fri"

    @pytest.mark.parametrize("day,expected", [(17, date(2026, 10, 16)), (18, date(2026, 10, 16))])
    def test_weekends_resolve_to_friday(self, day, expected):
        assert market_session_date(_utc(2026, 10, day, 18, 0)) == expected


class TestDefaults:
    def test_uses_now_when_not_given(self):
        session = market_session_date()

        assert isinstance(session, date)
        assert session.weekday() < 5, "a session date is never a weekend"
