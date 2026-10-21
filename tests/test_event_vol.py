"""
Tests for src/dashboard/event_vol.py.

Trade Ideas ranks by richness, and that ranking is dominated by names with
earnings coming -- implied vol rises before a binary event because the
event is real, so a pre-earnings name looks rich by construction. On
2026-10-20 that produced five Cash Secured Puts on five tickers that were
all the same trade. Sizing the event is what turns that from a hidden bias
into a label.

The property that matters most is that this returns None rather than a
number whenever the chain cannot support the calculation. A missing flag
is honest; a fabricated one teaches a trader to trust a figure that was
invented.
"""

from datetime import date, timedelta

import pandas as pd
import pytest

from src.dashboard.event_vol import implied_event_move

TODAY = date(2026, 10, 20)


def _chain(iv_by_dte: dict[int, float]) -> pd.DataFrame:
    """One ATM call per expiry at the given implied vols."""
    rows = []
    for dte, iv in iv_by_dte.items():
        for delta, strike in ((0.50, 100.0), (0.30, 110.0)):
            rows.append({
                "expiration": pd.Timestamp(TODAY + timedelta(days=dte)),
                "optionType": "CALL",
                "delta": delta,
                "strikePrice": strike,
                "impliedVolatility": iv,
            })
    return pd.DataFrame(rows)


def _live_dte(expiration) -> int:
    return (pd.Timestamp(expiration).date() - TODAY).days


class TestSizesTheEvent:
    def test_event_inside_the_nearest_expiry(self):
        """The front expiry is bid up and there is no event-free window
        before it -- the NFLX/META/TSLA case."""
        chain = _chain({3: 130.0, 10: 77.0, 31: 50.0, 59: 42.0})

        move = implied_event_move(chain, _live_dte, TODAY + timedelta(days=2), TODAY)

        assert move is not None
        assert move.before_dte is None, "no expiry settles before the event"
        assert move.after_dte == 3
        assert 5.0 < move.implied_move_pct < 25.0

    def test_event_bracketed_by_two_expiries(self):
        """The GOOGL/AMZN case: the near expiry misses the event, the next
        one contains it."""
        chain = _chain({3: 36.0, 10: 57.0, 17: 48.0, 31: 41.0, 59: 38.0})

        move = implied_event_move(chain, _live_dte, TODAY + timedelta(days=6), TODAY)

        assert move is not None
        assert (move.before_dte, move.after_dte) == (3, 10)
        assert 2.0 < move.implied_move_pct < 15.0

    def test_a_bigger_kink_implies_a_bigger_move(self):
        mild = implied_event_move(_chain({3: 30.0, 10: 34.0, 31: 28.0, 59: 27.0}),
                                  _live_dte, TODAY + timedelta(days=6), TODAY)
        wild = implied_event_move(_chain({3: 30.0, 10: 90.0, 31: 28.0, 59: 27.0}),
                                  _live_dte, TODAY + timedelta(days=6), TODAY)

        assert mild and wild
        assert wild.implied_move_pct > mild.implied_move_pct * 2


class TestRefusesToGuess:
    def test_flat_term_structure_has_no_event(self):
        """An index has no earnings. A flat curve must not be read as one --
        an earlier version reported a phantom 4.9% 'event' for SPX."""
        chain = _chain({3: 14.0, 10: 14.0, 31: 14.0, 59: 14.0})

        assert implied_event_move(chain, _live_dte, TODAY + timedelta(days=6), TODAY) is None

    def test_inverted_curve_has_no_event(self):
        """Front below back is the opposite of an event premium."""
        chain = _chain({3: 20.0, 10: 22.0, 31: 40.0, 59: 45.0})

        assert implied_event_move(chain, _live_dte, TODAY + timedelta(days=6), TODAY) is None

    def test_event_after_every_listed_expiry(self):
        chain = _chain({3: 40.0, 10: 38.0})

        assert implied_event_move(chain, _live_dte, TODAY + timedelta(days=90), TODAY) is None

    def test_event_already_past(self):
        chain = _chain({3: 90.0, 10: 50.0, 31: 40.0})

        assert implied_event_move(chain, _live_dte, TODAY - timedelta(days=1), TODAY) is None

    def test_too_few_expiries_for_a_baseline(self):
        chain = _chain({3: 90.0})

        assert implied_event_move(chain, _live_dte, TODAY + timedelta(days=2), TODAY) is None

    def test_empty_or_missing_chain(self):
        assert implied_event_move(None, _live_dte, TODAY, TODAY) is None
        assert implied_event_move(pd.DataFrame(), _live_dte, TODAY, TODAY) is None

    def test_sentinel_ivs_are_ignored(self):
        """Schwab writes -999 when it has no quote. Treating that as a vol
        would produce a spectacular and entirely fictional move."""
        chain = _chain({3: -999.0, 10: 40.0, 31: 38.0})

        move = implied_event_move(chain, _live_dte, TODAY + timedelta(days=2), TODAY)

        assert move is None or move.after_dte != 3
