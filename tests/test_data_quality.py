"""
Tests for src/data_quality.py's degenerate_metric_mask.

The values here aren't invented: they're taken from rows that really are in
the stored history. NFLX genuinely recorded 2687% implied vol with a skew of
926 on its expiration date, and APLD genuinely recorded a skew of 477 on a
527-DTE LEAPS. Both are what the maths does at the edges rather than bad data
from Schwab, and both wreck a chart's axis badly enough to flatten every
honest reading beside them -- NFLX's term-structure ceiling drops from 2687.3
to 46.2 once three rows are excluded.

The thresholds were calibrated so that only the unambiguous is caught, so the
"realistic values survive" tests matter as much as the catches: a filter that
quietly eats real data would be worse than the outliers it removes.
"""

import pandas as pd
import pytest

from src.data_quality import (
    MAX_PLAUSIBLE_CURVATURE,
    MAX_PLAUSIBLE_IV,
    MAX_PLAUSIBLE_SKEW,
    degenerate_metric_mask,
)


def frame(rows):
    return pd.DataFrame(rows, columns=["dte", "atm_iv", "skew", "curvature"])


class TestExpirationDayRows:
    def test_dte_zero_is_degenerate_regardless_of_values(self):
        """Implied vol stops being meaningful as time to expiry hits zero, so
        dte==0 is excluded on principle -- not because the numbers look odd."""
        df = frame([[0, 45.0, -2.0, 1.0]])
        assert degenerate_metric_mask(df).tolist() == [True]

    def test_real_nflx_expiry_day_row_is_caught(self):
        df = frame([[0, 2687.306, 926.43, 5.0]])
        assert degenerate_metric_mask(df).tolist() == [True]

    def test_dte_one_is_kept(self):
        """The boundary is exactly zero. One day out is volatile but real."""
        df = frame([[1, 188.9, -11.76, 3.0]])
        assert degenerate_metric_mask(df).tolist() == [False]


class TestImplausibleMagnitudes:
    def test_real_apld_leaps_outlier_is_caught(self):
        """The one non-expiry-day degenerate row in the whole history."""
        df = frame([[527, 94.4, 477.2, -239.8]])
        assert degenerate_metric_mask(df).tolist() == [True]

    @pytest.mark.parametrize(
        "row",
        [
            [30, MAX_PLAUSIBLE_IV + 1, -2.0, 1.0],
            [30, 50.0, MAX_PLAUSIBLE_SKEW + 1, 1.0],
            [30, 50.0, -(MAX_PLAUSIBLE_SKEW + 1), 1.0],
            [30, 50.0, -2.0, MAX_PLAUSIBLE_CURVATURE + 1],
            [30, 50.0, -2.0, -(MAX_PLAUSIBLE_CURVATURE + 1)],
        ],
        ids=["iv", "skew_high", "skew_low", "curvature_high", "curvature_low"],
    )
    def test_each_bound_is_enforced_in_both_directions(self, row):
        assert degenerate_metric_mask(frame([row])).tolist() == [True]

    @pytest.mark.parametrize(
        "row",
        [
            [30, 2687.3, -2.0, 1.0],
            [30, 50.0, 477.2, 1.0],
            [30, 50.0, -2.0, -239.8],
        ],
        ids=["nflx_iv_at_normal_dte", "apld_skew_at_normal_dte", "apld_curvature_at_normal_dte"],
    )
    def test_observed_bad_values_are_caught_on_their_own_merits(self, row):
        """Literal values, deliberately not expressed relative to the
        MAX_PLAUSIBLE_* constants: a test written as `MAX_PLAUSIBLE_IV + 1`
        moves with the threshold and so cannot detect the threshold being
        loosened. These are set at dte=30 so the dte==0 rule isn't what
        catches them -- each one pins its own bound.
        """
        assert degenerate_metric_mask(frame([row])).tolist() == [True]

    @pytest.mark.parametrize(
        "row",
        [
            [30, 12.5, -3.0, 2.0],
            [45, 104.0, -9.9, 4.4],
            [2, 188.9, 19.0, 22.6],
            [527, 60.0, -12.5, -3.5],
        ],
        ids=["spx_calm", "p95_readings", "observed_extremes", "long_dated_leaps"],
    )
    def test_realistic_readings_survive(self, row):
        """Real values observed in the stored history, including the most
        extreme ones that are still genuine. A false positive here means the
        filter is eating data a trader needs."""
        assert degenerate_metric_mask(frame([row])).tolist() == [False]


class TestRobustness:
    def test_empty_frame_returns_empty_mask(self):
        assert degenerate_metric_mask(pd.DataFrame()).empty

    def test_none_returns_empty_mask(self):
        assert degenerate_metric_mask(None).empty

    def test_missing_columns_are_ignored_not_fatal(self):
        """Callers apply this unconditionally across differently-shaped metric
        frames, so a frame without atm_iv/skew must not raise."""
        df = pd.DataFrame({"dte": [30, 0]})
        assert degenerate_metric_mask(df).tolist() == [False, True]

    def test_nulls_are_not_treated_as_degenerate(self):
        """A missing metric is a gap, not a degenerate value -- those are
        reported separately and shouldn't be silently dropped from charts."""
        df = frame([[30, None, None, None]])
        assert degenerate_metric_mask(df).tolist() == [False]

    def test_mask_aligns_with_original_index(self):
        """Callers use df[~mask], so a misaligned index would drop wrong rows."""
        df = frame([[0, 45.0, -2.0, 1.0], [30, 50.0, -2.0, 1.0]])
        df.index = [17, 42]
        mask = degenerate_metric_mask(df)
        assert mask.index.tolist() == [17, 42]
        assert df[~mask].index.tolist() == [42]

    def test_mixed_frame_flags_only_the_bad_rows(self):
        df = frame(
            [
                [30, 50.0, -2.0, 1.0],
                [0, 2687.3, 926.4, 5.0],
                [45, 60.0, -3.0, 2.0],
                [527, 94.4, 477.2, -239.8],
            ]
        )
        assert degenerate_metric_mask(df).tolist() == [False, True, False, True]
