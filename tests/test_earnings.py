"""
Tests for src/dashboard/earnings.py.

The calendar is hand-maintained, so the behaviour that matters is how it
degrades: a missing symbol, a typo'd date, or a file that is not there yet
must all produce "unknown" rather than an exception or a false all-clear.
An empty entry means nobody has filled it in -- never that no earnings are
due -- and the UI distinguishes those with has_calendar().
"""

import json
from datetime import date

import pytest

from src.dashboard import earnings


@pytest.fixture
def calendar_file(tmp_path):
    def write(mapping):
        p = tmp_path / "earnings_dates.json"
        p.write_text(json.dumps({"earnings": mapping}))
        earnings._load.cache_clear()
        return p
    yield write
    earnings._load.cache_clear()


TODAY = date(2026, 10, 20)


class TestNextEarnings:
    def test_finds_the_next_future_date(self, calendar_file):
        p = calendar_file({"AAPL": ["2026-07-31", "2026-10-30", "2027-01-29"]})

        assert earnings.next_earnings("AAPL", TODAY, p) == date(2026, 10, 30)

    def test_today_counts_as_upcoming(self, calendar_file):
        """An announcement today still sits before any future expiry."""
        p = calendar_file({"AAPL": ["2026-10-20"]})

        assert earnings.next_earnings("AAPL", TODAY, p) == TODAY

    def test_all_dates_past_returns_none(self, calendar_file):
        p = calendar_file({"AAPL": ["2026-01-30", "2026-04-30"]})

        assert earnings.next_earnings("AAPL", TODAY, p) is None

    def test_symbol_is_case_insensitive(self, calendar_file):
        p = calendar_file({"AAPL": ["2026-10-30"]})

        assert earnings.next_earnings("aapl", TODAY, p) == date(2026, 10, 30)


class TestDegradesToUnknown:
    def test_unknown_symbol(self, calendar_file):
        p = calendar_file({"AAPL": ["2026-10-30"]})

        assert earnings.next_earnings("ZZZZ", TODAY, p) is None
        assert earnings.has_calendar("ZZZZ", p) is False

    def test_empty_list_is_unknown_not_clear(self, calendar_file):
        """The distinction the UI depends on: an empty entry must not read
        as 'no earnings due'."""
        p = calendar_file({"AAPL": []})

        assert earnings.next_earnings("AAPL", TODAY, p) is None
        assert earnings.has_calendar("AAPL", p) is False

    def test_a_typo_does_not_blank_the_symbol(self, calendar_file):
        """One bad row must not discard the good ones beside it."""
        p = calendar_file({"AAPL": ["not-a-date", "2026-10-30", "2026-13-45"]})

        assert earnings.next_earnings("AAPL", TODAY, p) == date(2026, 10, 30)

    def test_missing_file(self, tmp_path):
        earnings._load.cache_clear()
        assert earnings.next_earnings("AAPL", TODAY, tmp_path / "absent.json") is None

    def test_malformed_file(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json")
        earnings._load.cache_clear()

        assert earnings.next_earnings("AAPL", TODAY, p) is None


class TestPastEarnings:
    def test_most_recent_first(self, calendar_file):
        p = calendar_file({"AAPL": ["2026-01-30", "2026-04-30", "2026-07-31", "2027-01-29"]})

        assert earnings.past_earnings("AAPL", TODAY, path=p) == [
            date(2026, 7, 31), date(2026, 4, 30), date(2026, 1, 30),
        ]

    def test_excludes_future_dates(self, calendar_file):
        p = calendar_file({"AAPL": ["2026-10-30", "2027-01-29"]})

        assert earnings.past_earnings("AAPL", TODAY, path=p) == []


class TestPicksUpEdits:
    def test_a_changed_file_is_reread(self, calendar_file):
        """The calendar is edited by hand; requiring a restart to see a new
        date would make it feel broken."""
        p = calendar_file({"AAPL": ["2026-10-30"]})
        assert earnings.next_earnings("AAPL", TODAY, p) == date(2026, 10, 30)

        import os, time
        p.write_text(json.dumps({"earnings": {"AAPL": ["2026-10-22"]}}))
        os.utime(p, (time.time() + 10, time.time() + 10))

        assert earnings.next_earnings("AAPL", TODAY, p) == date(2026, 10, 22)
