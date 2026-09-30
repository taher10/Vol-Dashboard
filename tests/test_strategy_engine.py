"""
Tests for src/dashboard/strategy_engine.py's calendar maths.

These exist because of a real defect found on 2026-09-30. The Calendar
leaderboard picked its two expirations by live DTE (routes._live_dte, today's
real date) but build_calendar_call() then read the chain's `dte` column,
which is frozen at fetch time. With a 7-day-old snapshot every leg was 7 days
longer than the page reported, so the forward-variance decomposition -- which
divides by (T_back - T_front) and weights each leg's variance by its own T --
ran on maturities nobody asked for. 22 of 24 rows' iv_ex could not be
reproduced from the DTEs displayed beside them.

That made it a silent ranking error rather than a visible one: the numbers
looked plausible, and only writing the derivation out on the page made the
mismatch checkable. The tests below pin both halves of the fix -- screening
uses live DTE, backtests keep the stored column -- because the two callers
genuinely want different answers and a single "correct" definition would
break one of them.
"""

import pandas as pd
import pytest

from src.dashboard.strategy_engine import build_calendar_call, calendar_variance_edge

FRONT = pd.Timestamp("2026-10-02")
BACK = pd.Timestamp("2026-10-23")

# Stored dte is deliberately 7 days longer than the "live" values below, the
# exact staleness that exposed the bug (snapshot 2026-09-23, viewed 09-30).
STORED_FRONT_DTE = 16
STORED_BACK_DTE = 37
LIVE_FRONT_DTE = 9
LIVE_BACK_DTE = 30

_LIVE = {FRONT: LIVE_FRONT_DTE, BACK: LIVE_BACK_DTE}


def _chain() -> pd.DataFrame:
    """Two expirations, three strikes each, front IV inflated over back."""
    rows = []
    for expiration, dte, iv, vega in (
        (FRONT, STORED_FRONT_DTE, 95.0, 0.20),
        (BACK, STORED_BACK_DTE, 88.0, 0.45),
    ):
        for strike, delta in ((28.0, 0.45), (30.0, 0.25), (32.0, 0.12)):
            rows.append({
                "optionType": "CALL",
                "expiration": expiration,
                "dte": dte,
                "strikePrice": strike,
                "delta": delta,
                "bid": 1.00,
                "ask": 1.10,
                "impliedVolatility": iv,
                "vega": vega,
                "underlyingPrice": 29.0,
            })
    return pd.DataFrame(rows)


def _expected_iv_ex(front_dte: int, back_dte: int) -> float:
    edge = calendar_variance_edge(95.0, front_dte, 0.20, 88.0, back_dte, 0.45)
    assert edge is not None
    return edge["iv_ex"]


class TestCalendarUsesTheCallersDteDefinition:
    def test_screening_uses_live_dte_not_the_frozen_column(self):
        """The defect: with dte_for supplied, the decomposition must run on
        the same maturities the caller picked and displays."""
        candidate = build_calendar_call(_chain(), FRONT, BACK, 0.25, dte_for=_LIVE.__getitem__)

        assert candidate is not None
        assert candidate.dte == LIVE_FRONT_DTE
        assert candidate.variance_edge["iv_ex"] == pytest.approx(
            _expected_iv_ex(LIVE_FRONT_DTE, LIVE_BACK_DTE)
        )

    def test_backtest_keeps_the_stored_column(self):
        """Without dte_for the stored value is the honest one -- it was
        correct on the entry date a backtest is replaying."""
        candidate = build_calendar_call(_chain(), FRONT, BACK, 0.25)

        assert candidate is not None
        assert candidate.dte == STORED_FRONT_DTE
        assert candidate.variance_edge["iv_ex"] == pytest.approx(
            _expected_iv_ex(STORED_FRONT_DTE, STORED_BACK_DTE)
        )

    def test_the_two_definitions_actually_disagree(self):
        """Guards the tests above: if stale and live DTEs produced the same
        iv_ex, neither test would be proving anything."""
        assert _expected_iv_ex(LIVE_FRONT_DTE, LIVE_BACK_DTE) != pytest.approx(
            _expected_iv_ex(STORED_FRONT_DTE, STORED_BACK_DTE)
        )

    def test_no_signal_when_live_dte_collapses_the_window(self):
        """A stale snapshot can put the front leg at or past the back leg
        once real dates are applied. There is no forward window to divide
        by, so this is 'no signal', not a crash or a fabricated number."""
        collapsed = {FRONT: 30, BACK: 30}

        assert build_calendar_call(_chain(), FRONT, BACK, 0.25, dte_for=collapsed.__getitem__) is None


class TestVarianceEdgeContract:
    def test_returns_none_when_front_is_not_inflated(self):
        """Back-month variance below front-month leaves nothing to decompose
        -- reported as no signal rather than an imaginary vol."""
        assert calendar_variance_edge(20.0, 9, 0.2, 10.0, 30, 0.4) is None

    def test_returns_none_without_a_forward_window(self):
        assert calendar_variance_edge(95.0, 30, 0.2, 88.0, 30, 0.4) is None

    def test_short_front_long_back_signs(self):
        """Short the front leg, so its crush is a gain; long the back leg, so
        its crush is a loss. Getting these backwards would invert the whole
        leaderboard."""
        edge = calendar_variance_edge(95.0, 9, 0.20, 88.0, 30, 0.45)

        assert edge is not None
        assert edge["front_crush"] > 0  # front has furthest to fall
        assert edge["front_vega_pnl"] > 0
        assert edge["back_vega_pnl"] < 0
        assert edge["net_vega_pnl"] == pytest.approx(edge["front_vega_pnl"] + edge["back_vega_pnl"])
