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

from src.dashboard.strategy_engine import (
    CONTRACT_MULTIPLIER,
    TWO_EXPIRATION_STRUCTURES,
    build_calendar_call,
    calendar_variance_edge,
)

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


# --------------------------------------------------------------------------
# same_strike: calendar vs diagonal
#
# Why a second fixture: _chain() above gives both expirations identical
# deltas, so delta-matching and strike-pinning pick the same strike and the
# two modes are indistinguishable. Every assertion below would pass against a
# build_calendar_call() that ignored same_strike entirely. This fixture moves
# the back month's smile so the modes genuinely diverge.
#
# The real-world motivation: delta-matched legs land on different strikes, so
# the "calendar" is actually a diagonal carrying a vertical. A screened SPX
# row (Nov 20/Dec 24, 7600/7800) showed a $605 debit against a $20,605 max
# loss -- the 200-point width, not the IV crush the edge metric describes.
# --------------------------------------------------------------------------

def _skewed_chain(back_strikes=((30.0, 0.55, 2.00, 2.10), (32.0, 0.40, 1.75, 1.85), (34.0, 0.25, 1.50, 1.60))):
    """Front and back months whose 0.25-delta calls sit on DIFFERENT strikes.

    Front 0.25d -> 30.0; back 0.25d -> 34.0, so delta-matching opens a
    4-point vertical while pinning keeps both legs at 30.0.
    """
    rows = []
    for strike, delta, bid, ask in ((28.0, 0.45, 1.40, 1.50), (30.0, 0.25, 1.00, 1.10), (32.0, 0.12, 0.60, 0.70)):
        rows.append({
            "optionType": "CALL", "expiration": FRONT, "dte": STORED_FRONT_DTE,
            "strikePrice": strike, "delta": delta, "bid": bid, "ask": ask,
            "impliedVolatility": 95.0, "vega": 0.20, "underlyingPrice": 29.0,
        })
    for strike, delta, bid, ask in back_strikes:
        rows.append({
            "optionType": "CALL", "expiration": BACK, "dte": STORED_BACK_DTE,
            "strikePrice": strike, "delta": delta, "bid": bid, "ask": ask,
            "impliedVolatility": 88.0, "vega": 0.45, "underlyingPrice": 29.0,
        })
    return pd.DataFrame(rows)


def _strikes(candidate):
    return candidate.legs[0].strike, candidate.legs[1].strike


class TestSameStrikeMode:
    def test_the_two_modes_actually_diverge(self):
        """Guards every other test in this class. If pinning and
        delta-matching picked the same strike on this fixture, none of them
        would be proving anything."""
        pinned = build_calendar_call(_skewed_chain(), FRONT, BACK, 0.25, same_strike=True)
        matched = build_calendar_call(_skewed_chain(), FRONT, BACK, 0.25, same_strike=False)

        assert _strikes(pinned) == (30.0, 30.0)
        assert _strikes(matched) == (30.0, 34.0)

    def test_pinned_legs_share_a_strike(self):
        candidate = build_calendar_call(_skewed_chain(), FRONT, BACK, 0.25, same_strike=True)

        front_strike, back_strike = _strikes(candidate)
        assert front_strike == back_strike

    def test_delta_matching_is_still_the_default(self):
        """The backtest calls this positionally and must keep replaying the
        structure it always has."""
        assert _strikes(build_calendar_call(_skewed_chain(), FRONT, BACK, 0.25)) == (30.0, 34.0)

    def test_pinned_max_loss_is_exactly_the_debit(self):
        """With one strike there is no width to lose, so the general bound
        collapses to the textbook calendar answer -- no special case needed."""
        candidate = build_calendar_call(_skewed_chain(), FRONT, BACK, 0.25, same_strike=True)

        assert candidate.net_debit_credit == pytest.approx(100.0)  # (2.05 - 1.05) * 100
        assert candidate.max_loss == pytest.approx(candidate.net_debit_credit)

    def test_delta_matched_max_loss_carries_the_strike_width(self):
        """The risk that made this option worth adding: a 4-point vertical
        turns a $50 debit into a $450 worst case, 9x the capital at stake."""
        candidate = build_calendar_call(_skewed_chain(), FRONT, BACK, 0.25, same_strike=False)

        assert candidate.net_debit_credit == pytest.approx(50.0)  # (1.55 - 1.05) * 100
        assert candidate.max_loss == pytest.approx(50.0 + 4.0 * CONTRACT_MULTIPLIER)

    def test_structures_are_named_for_what_they_are(self):
        pinned = build_calendar_call(_skewed_chain(), FRONT, BACK, 0.25, same_strike=True)
        matched = build_calendar_call(_skewed_chain(), FRONT, BACK, 0.25, same_strike=False)

        assert pinned.structure == "Calendar Call Spread"
        assert matched.structure == "Diagonal Call Spread"

    def test_both_labels_are_registered_as_two_expiration_structures(self):
        """backtest_engine branches on this set to decide a position never
        intrinsic-settles. A label that escaped it would be framed against a
        max_profit/max_loss these structures don't have."""
        assert {"Calendar Call Spread", "Diagonal Call Spread"} <= TWO_EXPIRATION_STRUCTURES

    def test_pinning_snaps_to_the_nearest_listed_strike(self):
        """Back months run coarser grids, so the front strike often isn't
        listed. Snapping beats returning None -- but the result is then a
        diagonal, and has to say so rather than inherit the requested name."""
        coarse = _skewed_chain(back_strikes=((28.0, 0.55, 2.00, 2.10), (36.0, 0.15, 1.00, 1.10)))

        candidate = build_calendar_call(coarse, FRONT, BACK, 0.25, same_strike=True)

        assert _strikes(candidate) == (30.0, 28.0)  # 28 is nearer to 30 than 36
        assert candidate.structure == "Diagonal Call Spread"

    def test_pinning_does_not_manufacture_a_signal(self):
        """Silence check. Pinning changes which strike is bought, never
        whether there is a decomposition to run.

        The no-signal case is an inversion steep enough that the front leg
        holds MORE total variance than the back (95^2*16 > 40^2*37), which
        implies negative forward variance. Pinning must still report nothing
        rather than an imaginary vol -- a mode that only ever adds rows to a
        leaderboard is a mode that will eventually add a wrong one."""
        inverted = _skewed_chain()
        inverted.loc[inverted["expiration"] == BACK, "impliedVolatility"] = 40.0

        candidate = build_calendar_call(inverted, FRONT, BACK, 0.25, same_strike=True)

        assert candidate is not None
        assert candidate.variance_edge is None
