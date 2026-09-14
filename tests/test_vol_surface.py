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
