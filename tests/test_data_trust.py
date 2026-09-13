"""
Tests for src/dashboard/data_trust.py -- the pipeline coverage report behind
the Data Trust page and src/validate_collection.py.

Several of these exist because the behaviour they pin down was wrong in a
shipped version. The weekend cases in particular: the coverage calendar was
originally built from weekdays alone, so when the pipeline was recovered by a
manually triggered Saturday run, the page still reported the last complete
run as eleven days earlier while every symbol had in fact reported that
morning. A page whose entire purpose is to not misrepresent data coverage was
misrepresenting it, and nothing caught that but a human looking at the screen.
"""

from datetime import date

import pytest

from src.dashboard import data_trust


# 2026-09-07 is a Monday, so 09-12/09-13 are Saturday/Sunday. Dates are
# hard-coded rather than derived from date.today() so these tests assert the
# same thing forever, including on the weekend.
MON = date(2026, 9, 7)
TUE = date(2026, 9, 8)
WED = date(2026, 9, 9)
THU = date(2026, 9, 10)
FRI = date(2026, 9, 11)
SAT = date(2026, 9, 12)


def coverage(**symbol_dates):
    """Build the {symbol: (color, dates)} shape build_trust_report expects."""
    return {sym: ("#000000", sorted(dates)) for sym, dates in symbol_dates.items()}


class TestTradingDays:
    def test_excludes_weekends(self):
        days = data_trust.trading_days(MON, date(2026, 9, 14))
        assert SAT not in days
        assert date(2026, 9, 13) not in days
        assert days == [MON, TUE, WED, THU, FRI, date(2026, 9, 14)]

    def test_single_weekend_day_range_is_empty(self):
        assert data_trust.trading_days(SAT, SAT) == []

    def test_inclusive_of_both_ends(self):
        assert data_trust.trading_days(MON, MON) == [MON]


class TestLargestMissingRun:
    def test_counts_longest_consecutive_gap_not_total_missing(self):
        calendar = [MON, TUE, WED, THU, FRI]
        covered = {MON, THU}  # gaps: TUE-WED (2), then FRI (1)
        assert data_trust._largest_missing_run(calendar, covered) == 2

    def test_zero_when_fully_covered(self):
        calendar = [MON, TUE, WED]
        assert data_trust._largest_missing_run(calendar, set(calendar)) == 0

    def test_whole_window_when_nothing_covered(self):
        calendar = [MON, TUE, WED]
        assert data_trust._largest_missing_run(calendar, set()) == 3


class TestOffScheduleRuns:
    """The shipped bug: a manually triggered weekend run was invisible."""

    def test_weekend_day_with_data_is_counted_as_a_complete_run(self):
        report = data_trust.build_trust_report(
            coverage(AAA=[FRI, SAT], BBB=[FRI, SAT]), today=SAT, window_trading_days=10
        )
        assert report["latest_complete_run"] == SAT.isoformat()
        assert report["trading_days_since_complete_run"] == 0

    def test_weekend_day_appears_in_the_displayed_calendar(self):
        report = data_trust.build_trust_report(
            coverage(AAA=[SAT]), today=SAT, window_trading_days=10
        )
        assert SAT.isoformat() in report["calendar"]

    def test_quiet_weekend_is_not_added_and_cannot_manufacture_a_gap(self):
        """Only days WITH data join the calendar. A weekend nobody ran on must
        not appear, or every Monday would report a two-day outage."""
        # Window of 2 so the calendar is exactly [THU, FRI], both covered --
        # isolates the weekend question from unrelated pre-history gaps.
        report = data_trust.build_trust_report(
            coverage(AAA=[THU, FRI]), today=FRI, window_trading_days=2
        )
        assert report["calendar"] == [THU.isoformat(), FRI.isoformat()]
        assert SAT.isoformat() not in report["calendar"]
        assert report["rows"][0]["largest_gap_trading_days"] == 0

    def test_age_is_never_negative_for_weekend_data(self):
        """Age was previously computed by recounting weekdays, which returned
        -1 when the newest data landed on a Saturday."""
        report = data_trust.build_trust_report(
            coverage(AAA=[SAT]), today=SAT, window_trading_days=10
        )
        assert report["rows"][0]["age_trading_days"] == 0


class TestCompleteRunThreshold:
    def test_partial_run_does_not_count_as_complete(self):
        """A handful of symbols reporting is a manual/partial run, not a
        pipeline day -- the real outage looked exactly like this."""
        report = data_trust.build_trust_report(
            coverage(A=[THU, FRI], B=[THU], C=[THU], D=[THU], E=[THU], F=[THU], G=[THU], H=[THU], I=[THU], J=[THU]),
            today=FRI,
            window_trading_days=10,
        )
        assert report["latest_complete_run"] == THU.isoformat()

    def test_no_complete_run_reports_none_not_a_guess(self):
        report = data_trust.build_trust_report(coverage(A=[], B=[]), today=FRI, window_trading_days=5)
        assert report["latest_complete_run"] is None
        assert report["trading_days_since_complete_run"] is None


class TestSymbolRows:
    def test_symbol_with_no_history_is_reported_not_dropped(self):
        report = data_trust.build_trust_report(coverage(GHOST=[]), today=FRI, window_trading_days=5)
        row = report["rows"][0]
        assert row["symbol"] == "GHOST"
        assert row["last_snapshot_date"] is None
        assert row["age_trading_days"] is None, "never-recorded must be None, not 0"
        assert row["n_observations"] == 0
        assert row["percentile_resolution_pts"] is None

    def test_percentile_resolution_is_100_over_n(self):
        report = data_trust.build_trust_report(
            coverage(AAA=[MON, TUE, WED, THU]), today=FRI, window_trading_days=10
        )
        assert report["rows"][0]["percentile_resolution_pts"] == pytest.approx(25.0)

    def test_age_counts_collection_days_not_calendar_days(self):
        """A Friday snapshot read on the following Monday is 1 day old, not 3."""
        report = data_trust.build_trust_report(
            coverage(AAA=[FRI]), today=date(2026, 9, 14), window_trading_days=10
        )
        assert report["rows"][0]["age_trading_days"] == 1

    def test_observations_count_all_history_not_just_the_window(self):
        old = date(2026, 1, 5)
        report = data_trust.build_trust_report(
            coverage(AAA=[old, FRI]), today=FRI, window_trading_days=5
        )
        row = report["rows"][0]
        assert row["n_observations"] == 2
        assert row["days_covered_in_window"] == 1
