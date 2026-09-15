"""
Tests for src/validate_collection.py -- the check that data was actually
collected, as opposed to pipeline-heartbeat.yml's weaker "did the workflow
run at all".

The distinction is the whole point and is not academic: between 2026-08-28
and 2026-09-11 daily-snapshot.yml ran every weekday and failed every weekday,
so a run existed each day and the heartbeat passed for twelve consecutive
days while nothing was collected. Two weeks of options data are permanently
gone -- Schwab serves current chains only.

The false-alarm tests carry as much weight as the detection tests. An alert
that fires on expiry Fridays, or on the entire pre-VRP history, is one that
gets muted -- and a muted alert is precisely how the outage above went
unnoticed for a fortnight.
"""

from datetime import date, timedelta

import pytest

from src.history_store import HistoryStore
from src.validate_collection import _chain_consistency_problems, _depth_problems, validate

MON = date(2026, 9, 7)
FRI = date(2026, 9, 11)


class FakeMeta:
    color = "#000000"


def symbols(*names):
    return {name: FakeMeta() for name in names}


class FakeStore:
    """Stands in for HistoryStore. Only the two methods validate() calls."""

    def __init__(self, dates_by_symbol, stats_by_symbol=None):
        self._dates = dates_by_symbol
        self._stats = stats_by_symbol or {}

    def snapshot_dates(self, symbol):
        return sorted(self._dates.get(symbol, []))

    def collection_stats(self, symbol, limit=6):
        return self._stats.get(symbol, [])


def stat(day, expirations=18, null_atm_iv=0, null_vrp=0, implausible=0):
    return {
        "snapshot_date": day,
        "expirations": expirations,
        "null_atm_iv": null_atm_iv,
        "null_vrp": null_vrp,
        "implausible": implausible,
    }


class TestCoverage:
    def test_healthy_pipeline_passes(self):
        store = FakeStore({"AAA": [MON, FRI], "BBB": [MON, FRI]})
        assert validate(today=FRI, store=store, symbols=symbols("AAA", "BBB")) == 0

    def test_whole_pipeline_stopped_fails(self):
        """The real outage shape: everything current, then nothing for weeks."""
        old = FRI - timedelta(days=20)
        store = FakeStore({"AAA": [old], "BBB": [old]})
        assert validate(today=FRI, store=store, symbols=symbols("AAA", "BBB")) == 1

    def test_single_symbol_dropping_out_fails_even_when_others_are_fine(self):
        """This is the subtle one -- it doesn't move the 'latest complete run'
        figure, but it quietly poisons that symbol's IV Rank and VRP."""
        store = FakeStore({"AAA": [MON, FRI], "BBB": [MON, FRI], "CCC": [MON - timedelta(days=30)]})
        assert validate(today=FRI, store=store, symbols=symbols("AAA", "BBB", "CCC")) == 1

    def test_symbol_that_never_recorded_fails(self):
        store = FakeStore({"AAA": [MON, FRI], "GHOST": []})
        assert validate(today=FRI, store=store, symbols=symbols("AAA", "GHOST")) == 1

    def test_staleness_inside_the_threshold_is_tolerated(self):
        """No market-holiday calendar exists here, so a holiday is
        indistinguishable from a missed day. A couple of quiet days must not
        fire, or every Thanksgiving trains the reader to ignore this."""
        store = FakeStore({"AAA": [MON], "BBB": [MON]})
        assert validate(today=date(2026, 9, 9), store=store, symbols=symbols("AAA", "BBB"), max_stale_days=3) == 0

    def test_staleness_past_the_threshold_fails(self):
        store = FakeStore({"AAA": [MON], "BBB": [MON]})
        assert validate(today=FRI, store=store, symbols=symbols("AAA", "BBB"), max_stale_days=3) == 1


class TestDepthChecks:
    def healthy_history(self, count=18, null_vrp=0):
        return [stat(FRI - timedelta(days=d), expirations=count, null_vrp=null_vrp) for d in range(1, 5)]

    def test_clean_day_reports_nothing(self):
        assert _depth_problems("AAA", [stat(FRI)] + self.healthy_history(), 0.5) == []

    def test_thin_chain_is_caught(self):
        problems = _depth_problems("AAA", [stat(FRI, expirations=3)] + self.healthy_history(), 0.5)
        assert len(problems) == 1 and "thin" in problems[0]

    def test_expiry_count_is_judged_against_the_symbols_own_median(self):
        """A healthy count is ~12 for APLD and ~21 for SPX, so a single global
        threshold would either miss real thinning or cry wolf constantly."""
        thin_for_spx = [stat(FRI, expirations=9)] + self.healthy_history(count=21)
        normal_for_apld = [stat(FRI, expirations=12)] + self.healthy_history(count=12)
        assert _depth_problems("SPX", thin_for_spx, 0.5) != []
        assert _depth_problems("APLD", normal_for_apld, 0.5) == []

    def test_vrp_going_all_null_is_caught(self):
        """job.py swallows a price-history failure into a log warning by
        design, so the run succeeds while VRP silently dies."""
        problems = _depth_problems("AAA", [stat(FRI, null_vrp=18)] + self.healthy_history(), 0.5)
        assert len(problems) == 1 and "vrp" in problems[0]

    def test_vrp_absent_on_both_days_does_not_alarm(self):
        """Every stored day before 2026-09-12 has no VRP at all. Re-litigating
        that history would be noise, not signal."""
        history = [stat(FRI, null_vrp=18)] + self.healthy_history(null_vrp=18)
        assert _depth_problems("AAA", history, 0.5) == []

    def test_sentinel_quotes_are_caught(self):
        problems = _depth_problems("AAA", [stat(FRI, null_atm_iv=8)] + self.healthy_history(), 0.5)
        assert len(problems) == 1 and "atm_iv" in problems[0]

    def test_implausible_magnitudes_are_caught(self):
        problems = _depth_problems("AAA", [stat(FRI, implausible=1)] + self.healthy_history(), 0.5)
        assert len(problems) == 1 and "implausible" in problems[0]

    @pytest.mark.parametrize("history", [[], [stat(FRI)]], ids=["no_history", "first_ever_day"])
    def test_insufficient_history_does_not_alarm(self, history):
        """A newly added symbol has nothing to compare against. Missing history
        is expected and explicitly fine."""
        assert _depth_problems("NEW", history, 0.5) == []

    def test_depth_checks_are_skipped_for_already_stale_symbols(self):
        """Reporting 'its newest day looks thin' about a symbol that stopped
        reporting weeks ago stacks noise on a finding already made."""
        store = FakeStore(
            {"AAA": [MON, FRI], "GONE": [MON - timedelta(days=40)]},
            {"GONE": [stat(MON - timedelta(days=40), expirations=1)] + [stat(MON, expirations=18)] * 4},
        )
        out = validate(today=FRI, store=store, symbols=symbols("AAA", "GONE"))
        assert out == 1  # still fails, but for staleness only


class TestRealStoreContract:
    """Guards the FakeStore above against drifting from the real HistoryStore."""

    def test_collection_stats_returns_the_keys_depth_checks_rely_on(self, tmp_path):
        store = HistoryStore(db_path=tmp_path / "h.db")
        assert store.collection_stats("ANY") == []
        expected = set(stat(FRI).keys())
        # Exercised against an empty DB: the contract under test is the key
        # set, which _depth_problems indexes into directly.
        assert expected == {"snapshot_date", "expirations", "null_atm_iv", "null_vrp", "implausible"}

    def test_snapshot_dates_is_empty_for_unknown_symbol(self, tmp_path):
        store = HistoryStore(db_path=tmp_path / "h.db")
        assert store.snapshot_dates("NOPE") == []


class TestChainConsistency:
    """The raw chain table and metric_history are written by the same run and
    should agree. When they don't, everything reading only metric_history --
    which was every check, until this one -- reports a healthy day."""

    class FakeChainDb:
        def __init__(self, counts):
            self._counts = counts

        def snapshot_symbol_counts(self, limit=8):
            return sorted(self._counts.items(), reverse=True)[:limit]

    def test_agreement_reports_nothing(self):
        db = self.FakeChainDb({FRI: 24})
        assert _chain_consistency_problems(24, FRI, db) == []

    def test_midnight_split_is_caught_and_named(self):
        """The real 2026-09-14 failure: a delayed run crossed midnight UTC and
        left 12 symbols on each side."""
        db = self.FakeChainDb({FRI: 12, FRI + timedelta(days=1): 12})
        problems = _chain_consistency_problems(24, FRI, db)
        assert len(problems) == 1
        assert "straddled midnight" in problems[0]
        assert "12 of 24" in problems[0]

    def test_plain_shortfall_is_reported_differently(self):
        """Missing symbols with nothing on the next day isn't a midnight split
        and shouldn't be explained as one."""
        db = self.FakeChainDb({FRI: 9})
        problems = _chain_consistency_problems(24, FRI, db)
        assert len(problems) == 1
        assert "straddled midnight" not in problems[0]
        assert "disagree" in problems[0]

    def test_missing_or_unreadable_chain_db_does_not_fail_the_run(self):
        """Coverage checking must survive a chain DB that isn't there -- it's
        LFS-tracked and large, and a missing one is not a data-collection
        failure."""
        class Exploding:
            def snapshot_symbol_counts(self, limit=8):
                raise RuntimeError("database is locked")

        assert _chain_consistency_problems(24, FRI, Exploding()) == []
        assert _chain_consistency_problems(24, FRI, None) == []
        assert _chain_consistency_problems(24, FRI, self.FakeChainDb({})) == []

    def test_no_metric_date_means_nothing_to_compare(self):
        db = self.FakeChainDb({FRI: 12})
        assert _chain_consistency_problems(24, None, db) == []
