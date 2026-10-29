"""
Tests for src/synthetic_market.py.

The demo exists so the dashboard can be shown to people without
redistributing anyone's market data. Two properties decide whether it
works: the data has to be entirely generated, and it has to be realistic
enough that the dashboard's own logic has something to act on. A flat,
perfectly-quoted surface would render, and would demonstrate nothing.
"""

import math
from datetime import date

import pytest

from src import synthetic_market as sm
from src.symbols import SYMBOL_REGISTRY

AS_OF = date(2026, 10, 28)


class TestBlackScholes:
    """Priced from scratch, so the usual identities are worth pinning."""

    def test_put_call_parity(self):
        s, k, t, v = 100.0, 95.0, 0.5, 0.30
        call = sm.black_scholes(s, k, t, v, is_call=True).price
        put = sm.black_scholes(s, k, t, v, is_call=False).price

        assert call - put == pytest.approx(s - k * math.exp(-sm.RISK_FREE * t), abs=1e-6)

    def test_deltas_are_bounded_and_opposite(self):
        call = sm.black_scholes(100.0, 100.0, 0.25, 0.3, is_call=True)
        put = sm.black_scholes(100.0, 100.0, 0.25, 0.3, is_call=False)

        assert 0.0 < call.delta < 1.0
        assert -1.0 < put.delta < 0.0
        assert call.delta - put.delta == pytest.approx(1.0, abs=1e-9)

    def test_vega_is_per_vol_point(self):
        """The rest of the project multiplies vega by a vol-point move and
        by 100 for the contract, so the units have to match the broker's."""
        g = sm.black_scholes(100.0, 100.0, 1.0, 0.20, is_call=True)
        bumped = sm.black_scholes(100.0, 100.0, 1.0, 0.21, is_call=True)

        assert g.vega == pytest.approx(bumped.price - g.price, rel=0.02)

    def test_expiry_returns_intrinsic_rather_than_raising(self):
        assert sm.black_scholes(110.0, 100.0, 0.0, 0.3, is_call=True).price == pytest.approx(10.0)
        assert sm.black_scholes(90.0, 100.0, 0.0, 0.3, is_call=True).price == 0.0


class TestSurfaceShape:
    """The stylised facts the dashboard's panels are built to detect."""

    def _p(self):
        return sm.SurfaceParams(vol_long=30.0, vol_short=36.0, skew=4.0, curvature=7.0)

    def test_puts_are_bid_over_calls(self):
        p = self._p()
        put_wing = sm.implied_vol(math.log(0.90), 0.25, p, AS_OF)
        call_wing = sm.implied_vol(math.log(1.10), 0.25, p, AS_OF)

        assert put_wing > call_wing

    def test_skew_flattens_with_maturity(self):
        """Steep at a week, nearly linear at a year. The 25-delta skew
        column is a statement about this."""
        p = self._p()

        def slope(years):
            lo = sm.implied_vol(math.log(0.95), years, p, AS_OF)
            hi = sm.implied_vol(math.log(1.05), years, p, AS_OF)
            return lo - hi

        assert slope(7 / 365) > slope(90 / 365) > slope(365 / 365)

    def test_the_wings_do_not_diverge(self):
        """An earlier quadratic-in-z smile put four-day wings at 219 vol
        points and pinned the surface against its cap."""
        p = self._p()
        wing = sm.implied_vol(math.log(0.75), 3 / 365, p, AS_OF)

        assert wing < 3.5 * p.vol_short

    def test_the_cap_does_not_create_flat_spots(self):
        """Identical IVs across adjacent strikes read as generated data."""
        p = self._p()
        vols = [sm.implied_vol(math.log(1 - i / 100), 3 / 365, p, AS_OF) for i in range(15, 25)]

        assert len(set(round(v, 4) for v in vols)) == len(vols)

    def test_an_event_lifts_the_expirations_that_contain_it(self):
        """And not the ones before it -- that asymmetry is the whole basis
        of the forward-variance decomposition."""
        p = sm.SurfaceParams(
            vol_long=30.0, vol_short=30.0, event_bump=8.0, event_date=AS_OF + date.resolution * 18
        )
        before = sm.implied_vol(0.0, 10 / 365, p, AS_OF)
        after = sm.implied_vol(0.0, 25 / 365, p, AS_OF)

        assert after > before


class TestQuotes:
    def test_wings_near_expiry_lose_their_precision(self):
        """The condition vol_surface's quote filter exists to find: a cheap,
        far, short-dated contract whose spread spans many vol points."""
        g = sm.black_scholes(318.0, 250.0, 4 / 365, 0.90, is_call=False)
        bid, ask = sm.quote(g.price, g.vega, math.log(250 / 318), 4 / 365, sm.random.Random(0))

        assert (ask - bid) / g.vega > 5.0

    def test_at_the_money_quotes_are_tight_in_vol_terms(self):
        g = sm.black_scholes(318.0, 318.0, 30 / 365, 0.30, is_call=True)
        bid, ask = sm.quote(g.price, g.vega, 0.0, 30 / 365, sm.random.Random(0))

        assert 0 < (ask - bid) / g.vega < 3.0

    def test_bid_never_goes_negative(self):
        bid, ask = sm.quote(0.004, 0.0005, math.log(0.7), 2 / 365, sm.random.Random(0))

        assert bid >= 0.0
        assert ask > bid


class TestGeneratedChain:
    def test_every_registry_symbol_is_modelled(self):
        """A symbol in the registry with no simulated data appears in the UI
        and then fails to load, and precompute silently skips it."""
        modelled = {s.symbol for s in sm.UNIVERSE}

        assert modelled == set(SYMBOL_REGISTRY)

    def test_it_is_deterministic(self):
        """Screenshots have to be reproducible, and a bug in a demo that
        reshuffles itself on every build cannot be chased."""
        spec = sm.UNIVERSE[0]
        a = sm.generate_chain(spec, AS_OF, spec.spot)
        b = sm.generate_chain(spec, AS_OF, spec.spot)

        assert a == b

    def test_chains_carry_what_the_dashboard_reads(self):
        spec = [s for s in sm.UNIVERSE if s.symbol == "GOOGL"][0]
        rows = sm.generate_chain(spec, AS_OF, spec.spot)

        assert rows
        needed = {"bid", "ask", "vega", "delta", "implied_volatility", "strike_price", "dte"}
        assert needed.issubset(rows[0])
        assert all(r["dte"] > 0 for r in rows)
        assert all(r["ask"] >= r["bid"] >= 0 for r in rows)

    def test_implied_vols_stay_in_a_believable_range(self):
        for spec in sm.UNIVERSE:
            ivs = [r["implied_volatility"] for r in sm.generate_chain(spec, AS_OF, spec.spot)]

            assert min(ivs) > 4.0, spec.symbol
            assert max(ivs) < 200.0, spec.symbol


class TestPricePath:
    """The chain reads its spot from this series, so the two have to agree.

    They did not at first: the dates advanced a day at a time while
    skipping weekends, so 400 steps spanned about 560 calendar days, the
    tail of the path landed in the future, and the chain picked a spot from
    the middle of the walk. SPX was specified at 6900 and the dashboard
    rendered 6298.
    """

    def test_it_ends_on_the_requested_date_at_the_specified_spot(self):
        spec = [s for s in sm.UNIVERSE if s.symbol == "SPX"][0]

        path = sm.price_path(spec, days=400, end=AS_OF)

        assert path[-1][0] == AS_OF
        assert path[-1][4] == pytest.approx(spec.spot)

    def test_it_never_runs_into_the_future(self):
        spec = sm.UNIVERSE[0]

        path = sm.price_path(spec, days=250, end=AS_OF)

        assert all(day <= AS_OF for day, *_ in path)

    def test_sessions_are_weekdays_in_order(self):
        spec = sm.UNIVERSE[0]

        days = [d for d, *_ in sm.price_path(spec, days=60, end=AS_OF)]

        assert days == sorted(days)
        assert all(d.weekday() < 5 for d in days)

    def test_highs_and_lows_bracket_the_close(self):
        for spec in sm.UNIVERSE[:5]:
            for _, open_, high, low, close in sm.price_path(spec, days=40, end=AS_OF):
                assert low <= min(open_, close) and high >= max(open_, close), spec.symbol
