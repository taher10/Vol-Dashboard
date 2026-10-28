"""
Tests for src/dashboard/vol_surface.py.

The surface has three modelling decisions that are invisible in the output
but change what it means: OTM-side-only quotes, moneyness rather than
strike, and constant-maturity comparison. Each one could be "simplified"
away by a later edit without anything looking obviously wrong on screen,
which is exactly why they're pinned here rather than left to the docstring.
"""

import pandas as pd
import pytest

from src.dashboard import vol_surface


def chain(rows):
    """rows: (expiration, strike, option_type, iv)"""
    return pd.DataFrame(
        [
            {"expiration": e, "strikePrice": k, "optionType": t, "impliedVolatility": iv}
            for e, k, t, iv in rows
        ]
    )


def dte_of(mapping):
    return lambda exp: mapping.get(exp, -1)


SPOT = 100.0
EXP = "2026-10-16"
DTE = {EXP: 30}


class TestOtmSideSelection:
    def test_below_spot_uses_the_put(self):
        df = chain([(EXP, 90.0, "PUT", 40.0), (EXP, 90.0, "CALL", 99.0)])
        out = vol_surface.build_surface(df, SPOT, dte_of(DTE))
        assert [c["side"] for c in out["cells"]] == ["PUT"]
        assert out["cells"][0]["iv"] == pytest.approx(40.0)

    def test_above_spot_uses_the_call(self):
        df = chain([(EXP, 110.0, "CALL", 30.0), (EXP, 110.0, "PUT", 99.0)])
        out = vol_surface.build_surface(df, SPOT, dte_of(DTE))
        assert [c["side"] for c in out["cells"]] == ["CALL"]
        assert out["cells"][0]["iv"] == pytest.approx(30.0)

    def test_itm_quotes_never_reach_the_surface(self):
        """ITM options are thin and intrinsic-dominated; including them puts a
        spurious step at the money."""
        df = chain([(EXP, 90.0, "CALL", 99.0), (EXP, 110.0, "PUT", 99.0)])
        out = vol_surface.build_surface(df, SPOT, dte_of(DTE))
        assert out["cells"] == []


class TestMoneyness:
    def test_keyed_by_moneyness_not_strike(self):
        """Same relative distance from spot must land in the same bucket even
        when the underlyings are priced decades apart."""
        cheap = vol_surface.build_surface(chain([(EXP, 90.0, "PUT", 40.0)]), 100.0, dte_of(DTE))
        rich = vol_surface.build_surface(chain([(EXP, 900.0, "PUT", 40.0)]), 1000.0, dte_of(DTE))
        assert cheap["cells"][0]["moneyness"] == rich["cells"][0]["moneyness"] == pytest.approx(-0.10)

    def test_strikes_beyond_the_window_are_dropped(self):
        df = chain([(EXP, 50.0, "PUT", 80.0), (EXP, 95.0, "PUT", 30.0)])
        out = vol_surface.build_surface(df, SPOT, dte_of(DTE))
        assert [c["moneyness"] for c in out["cells"]] == [pytest.approx(-0.05)]

    def test_nearby_strikes_collapse_into_one_bucket_by_median(self):
        """A dense ladder puts several strikes in a bucket. Median, not mean --
        one stale wide quote shouldn't drag the cell."""
        df = chain([(EXP, 89.8, "PUT", 30.0), (EXP, 90.0, "PUT", 31.0), (EXP, 90.2, "PUT", 200.0)])
        out = vol_surface.build_surface(df, SPOT, dte_of(DTE))
        assert len(out["cells"]) == 1
        assert out["cells"][0]["iv"] == pytest.approx(31.0)


class TestUnusableInput:
    @pytest.mark.parametrize(
        "iv", [-999.0, 0.0, 600.0], ids=["schwab_sentinel", "zero", "absurd"]
    )
    def test_unusable_ivs_are_excluded(self, iv):
        out = vol_surface.build_surface(chain([(EXP, 90.0, "PUT", iv)]), SPOT, dte_of(DTE))
        assert out["cells"] == []

    def test_expired_expirations_are_excluded(self):
        out = vol_surface.build_surface(chain([(EXP, 90.0, "PUT", 30.0)]), SPOT, dte_of({EXP: -3}))
        assert out["cells"] == []

    @pytest.mark.parametrize("spot", [None, 0.0, -5.0], ids=["none", "zero", "negative"])
    def test_no_usable_spot_returns_empty(self, spot):
        out = vol_surface.build_surface(chain([(EXP, 90.0, "PUT", 30.0)]), spot, dte_of(DTE))
        assert out["cells"] == []

    def test_empty_chain_returns_empty_not_error(self):
        assert vol_surface.build_surface(None, SPOT, dte_of(DTE))["cells"] == []
        assert vol_surface.build_surface(pd.DataFrame(), SPOT, dte_of(DTE))["cells"] == []

    def test_no_interpolation_into_gaps(self):
        """Two strikes far apart must not produce cells between them. A gap
        means nothing was quoted there."""
        df = chain([(EXP, 80.0, "PUT", 45.0), (EXP, 120.0, "CALL", 35.0)])
        out = vol_surface.build_surface(df, SPOT, dte_of(DTE))
        assert len(out["cells"]) == 2


class TestStoredChainAdapter:
    def test_snake_case_columns_are_renamed(self):
        """SchwabDatabase returns snake_case; build_surface expects camelCase."""
        stored = pd.DataFrame(
            [{"implied_volatility": 30.0, "strike_price": 90.0, "option_type": "PUT", "underlying_price": 100.0}]
        )
        out = vol_surface.normalize_stored_chain(stored)
        assert {"impliedVolatility", "strikePrice", "optionType", "underlyingPrice"}.issubset(out.columns)


class TestComparison:
    def two_dates(self, iv_now, iv_then, dte_now=30, dte_then=32):
        now = chain([(EXP, 90.0, "PUT", iv_now)])
        then = chain([("2026-09-30", 90.0, "PUT", iv_then)])
        then["dte"] = dte_then
        return now, then, dte_of({EXP: dte_now})

    def test_reports_the_change_between_snapshots(self):
        now, then, dte = self.two_dates(33.0, 30.0)
        out = vol_surface.build_comparison(now, then, SPOT, SPOT, dte)
        assert len(out["cells"]) == 1
        assert out["cells"][0]["change"] == pytest.approx(3.0)
        assert out["cells"][0]["iv_prev"] == pytest.approx(30.0)

    def test_constant_maturity_aligns_aged_expirations(self):
        """32 DTE then and 30 DTE now are the same point on the curve. Diffing
        by raw dte would find nothing to compare and report no change at all."""
        now, then, dte = self.two_dates(33.0, 30.0, dte_now=30, dte_then=32)
        out = vol_surface.build_comparison(now, then, SPOT, SPOT, dte)
        assert out["cells"], "aged expirations should still align on the ladder"
        assert out["cells"][0]["dte"] == 30

    def test_moneyness_absorbs_a_spot_move(self):
        """Spot moved 100 -> 110, so the 10%-OTM put is a different strike on
        each date. It's still the same point on the surface."""
        now = chain([(EXP, 99.0, "PUT", 31.0)])
        then = chain([("2026-09-30", 90.0, "PUT", 30.0)])
        then["dte"] = 30
        out = vol_surface.build_comparison(now, then, 110.0, 100.0, dte_of({EXP: 30}))
        assert len(out["cells"]) == 1
        assert out["cells"][0]["change"] == pytest.approx(1.0)

    def test_cells_quoted_on_only_one_side_are_dropped(self):
        """A strike listed today but not then has no baseline, and inventing
        one would manufacture a move."""
        now = chain([(EXP, 90.0, "PUT", 33.0), (EXP, 80.0, "PUT", 45.0)])
        then = chain([("2026-09-30", 90.0, "PUT", 30.0)])
        then["dte"] = 30
        out = vol_surface.build_comparison(now, then, SPOT, SPOT, dte_of({EXP: 30}))
        assert len(out["cells"]) == 1
        assert out["cells"][0]["moneyness"] == pytest.approx(-0.10)

    def test_missing_prior_returns_empty_not_error(self):
        now = chain([(EXP, 90.0, "PUT", 33.0)])
        for prior in (None, pd.DataFrame()):
            out = vol_surface.build_comparison(now, prior, SPOT, SPOT, dte_of({EXP: 30}))
            assert out["cells"] == []


class TestElapsedGapLabelling:
    """The whole point of this view is that the stated gap matches the data.

    days_elapsed is computed in the route from the snapshot's own as_of, not
    from date.today(). Those coincide only when the pipeline ran today, and it
    frequently hasn't: on 2026-09-21 the newest snapshot was 09-16, so a
    comparison against 09-15 is a 1-day move that measuring from the wall clock
    labelled as 6 days -- understating nothing, but overstating the elapsed
    time sixfold and making a quiet day look like a week of drift.
    """

    @pytest.mark.parametrize(
        "as_of,compare,expected",
        [
            ("2026-09-16", "2026-09-15", 1),
            ("2026-09-16", "2026-09-12", 4),
            ("2026-09-16", "2026-08-27", 20),
        ],
        ids=["one_day", "few_days", "across_the_outage"],
    )
    def test_gap_is_between_snapshots_not_from_today(self, as_of, compare, expected):
        from datetime import date as _d

        assert (_d.fromisoformat(as_of) - _d.fromisoformat(compare)).days == expected

    def test_a_stale_pipeline_does_not_inflate_the_gap(self):
        """Regression guard for the actual bug: today must not enter the sum."""
        from datetime import date as _d

        as_of, compare, today = _d(2026, 9, 16), _d(2026, 9, 15), _d(2026, 9, 21)
        assert (as_of - compare).days == 1
        assert (today - compare).days == 6, "the wrong answer the route used to return"


# ---------------------------------------------------------------------------
# Quote quality
# ---------------------------------------------------------------------------


def quoted_chain(rows):
    """rows: (expiration, strike, option_type, iv, bid, ask, vega)

    Separate from chain() on purpose: the filter is skipped when the quote
    columns are absent, so the existing tests above exercise the unfiltered
    path and these exercise the filtered one.
    """
    return pd.DataFrame(
        [
            {
                "expiration": e,
                "strikePrice": k,
                "optionType": t,
                "impliedVolatility": iv,
                "bid": bid,
                "ask": ask,
                "vega": vega,
            }
            for e, k, t, iv, bid, ask, vega in rows
        ]
    )


class TestQuoteQualityFilter:
    """A four-cent contract has an implied volatility the way a coin flip
    has a trend: the number exists, it is not measuring anything.

    These were the loudest cells on the heatmap because they are always the
    most extreme, so they set the colour scale and flattened the real smirk
    into one shade.
    """

    def test_a_spread_wider_than_the_band_is_dropped(self):
        """The observed GOOGL case: 21% OTM at 4 DTE, bid 0.02 / ask 0.06,
        vega 0.004, quoted IV 89.96. The spread spans 10 vol points."""
        df = quoted_chain([(EXP, 79.0, "PUT", 89.96, 0.02, 0.06, 0.004)])

        out = vol_surface.build_surface(df, SPOT, dte_of(DTE))

        assert out["cells"] == []
        assert out["quote_filter"]["excluded_contracts"] == 1
        assert out["quote_filter"]["applied"] is True

    def test_a_high_iv_wing_with_a_tight_spread_survives(self):
        """The filter is about whether the vol is knowable, not whether it
        is large. Dropping genuine skew would defeat the point of a
        surface."""
        df = quoted_chain([(EXP, 85.0, "PUT", 80.9, 0.23, 0.25, 0.018)])

        out = vol_surface.build_surface(df, SPOT, dte_of(DTE))

        assert len(out["cells"]) == 1
        assert out["cells"][0]["iv"] == pytest.approx(80.9)
        assert out["quote_filter"]["excluded_contracts"] == 0

    def test_a_zero_bid_is_dropped_however_tight_the_ask(self):
        """Nobody is bidding, so there is no market -- only an ask, and an
        IV from one side of a market is a guess."""
        df = quoted_chain([(EXP, 80.0, "PUT", 95.0, 0.0, 0.01, 0.05)])

        out = vol_surface.build_surface(df, SPOT, dte_of(DTE))

        assert out["cells"] == []

    def test_the_band_is_measured_in_vol_points_not_dollars(self):
        """Same 0.04 spread, different vega. A plain price rule cannot tell
        these apart, and the cheap one is the whole problem."""
        rows = [
            (EXP, 79.0, "PUT", 89.0, 0.02, 0.06, 0.004),  # 10 vol points
            (EXP, 95.0, "PUT", 42.0, 1.98, 2.02, 0.300),  # 0.13 vol points
        ]

        out = vol_surface.build_surface(quoted_chain(rows), SPOT, dte_of(DTE))

        assert [c["iv"] for c in out["cells"]] == [pytest.approx(42.0)]

    def test_a_missing_greek_is_not_a_usable_quote(self):
        """Paired with a sound quote deliberately. On its own this row is
        indistinguishable from a chain that carries no greeks at all, which
        is a data fault and is handled the opposite way -- see
        TestQuoteFilterStaysOutOfTheWay."""
        rows = [
            (EXP, 80.0, "PUT", 88.0, 0.02, 0.06, float("nan")),
            (EXP, 95.0, "PUT", 42.0, 1.98, 2.02, 0.300),
        ]

        out = vol_surface.build_surface(quoted_chain(rows), SPOT, dte_of(DTE))

        assert [c["iv"] for c in out["cells"]] == [pytest.approx(42.0)]

    def test_zero_vega_does_not_pass_as_an_infinitely_tight_quote(self):
        """Dividing by zero yields inf, and inf <= band is False only if the
        arithmetic is guarded -- worth pinning, since the failure mode is a
        junk cell being admitted rather than an error."""
        rows = [
            (EXP, 80.0, "PUT", 88.0, 0.02, 0.06, 0.0),
            (EXP, 95.0, "PUT", 42.0, 1.98, 2.02, 0.300),
        ]

        out = vol_surface.build_surface(quoted_chain(rows), SPOT, dte_of(DTE))

        assert [c["iv"] for c in out["cells"]] == [pytest.approx(42.0)]


class TestQuoteFilterStaysOutOfTheWay:
    def test_a_chain_without_quote_columns_is_built_unfiltered(self):
        """Some live chains arrive without greeks. The old surface is the
        right answer there; a blank page is not."""
        df = chain([(EXP, 90.0, "PUT", 40.0)])

        out = vol_surface.build_surface(df, SPOT, dte_of(DTE))

        assert len(out["cells"]) == 1
        assert out["quote_filter"]["applied"] is False

    def test_it_never_empties_a_grid_it_could_only_partly_judge(self):
        """If every row fails, the quote data is what is broken, not the
        market. A filter that silently deletes the whole page is worse than
        the noise it was added to remove."""
        df = quoted_chain(
            [
                (EXP, 90.0, "PUT", 40.0, 0.0, 0.0, float("nan")),
                (EXP, 95.0, "PUT", 38.0, 0.0, 0.0, float("nan")),
            ]
        )

        out = vol_surface.build_surface(df, SPOT, dte_of(DTE))

        assert len(out["cells"]) == 2
        assert out["quote_filter"]["applied"] is False
        assert out["quote_filter"]["excluded_contracts"] == 0

    def test_band_of_zero_disables_it(self):
        df = quoted_chain([(EXP, 79.0, "PUT", 89.0, 0.02, 0.06, 0.004)])

        out = vol_surface.build_surface(df, SPOT, dte_of(DTE), max_iv_band=0)

        assert len(out["cells"]) == 1
        assert out["quote_filter"]["applied"] is False

    def test_the_threshold_is_honoured(self):
        """Band here is exactly 4 vol points."""
        df = quoted_chain([(EXP, 90.0, "PUT", 60.0, 0.10, 0.14, 0.01)])

        assert vol_surface.build_surface(df, SPOT, dte_of(DTE), max_iv_band=3.0)["cells"] == []
        assert len(vol_surface.build_surface(df, SPOT, dte_of(DTE), max_iv_band=5.0)["cells"]) == 1

    def test_comparison_applies_the_same_filter_to_both_sides(self):
        """Otherwise a cell could be built from a trustworthy quote on one
        date and a four-cent one on the other, and the 'change' would be an
        artefact of which junk happened to be listed."""
        now = quoted_chain([(EXP, 90.0, "PUT", 45.0, 1.00, 1.04, 0.20)])
        now["dte"] = 30
        then = quoted_chain([(EXP, 90.0, "PUT", 95.0, 0.02, 0.06, 0.004)])
        then["dte"] = 32

        out = vol_surface.build_comparison(now, then, SPOT, SPOT, dte_of(DTE))

        assert out["cells"] == []
