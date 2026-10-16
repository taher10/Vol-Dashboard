"""
Tests for src/api/warmup.py.

The row cache made a warm leaderboard ~0.15s on the public demo, but the
first request after the process starts still did the real work -- ~18s for
the Vol Scanner on a free-tier CPU -- and the instance sleeps after 15
minutes, so someone paid that again on every visit after a quiet spell.
Warming in the background during the ~50s Render already spends waking the
container costs the visitor nothing.

The subtle failure this guards is a parameter mismatch. The cache is keyed
on arguments, so warming (30, 0.30) when the page requests (30, 0.50)
populates an entry nobody wants and leaves the real one cold -- with no
error, no log, and no symptom except the page still being slow. That
happened: /scanner/delta-neutral stayed at 0.42s through the first version
of this module because its default delta is 0.50, not 0.30.

So these assert the warm constants against the endpoints' actual Query()
defaults, read from the live signatures rather than copied.
"""

import inspect

import pytest

from src.api import routes, warmup


def _query_default(fn, param: str):
    """The Query(...) default an endpoint actually declares."""
    default = inspect.signature(fn).parameters[param].default
    return getattr(default, "default", default)


class TestWarmConstantsMatchTheEndpoints:
    def test_scanner_target_dte(self):
        assert warmup._DEFAULT_TARGET_DTE == _query_default(routes.scanner, "target_dte")

    def test_calendar_edge(self):
        front, back, delta = warmup._DEFAULT_CALENDAR
        assert front == _query_default(routes.scanner_calendar_edge, "front_dte")
        assert back == _query_default(routes.scanner_calendar_edge, "back_dte")
        assert delta == _query_default(routes.scanner_calendar_edge, "target_delta")

    def test_term_structure(self):
        near, far = warmup._DEFAULT_TERM_STRUCTURE
        assert near == _query_default(routes.scanner_term_structure, "near_dte")
        assert far == _query_default(routes.scanner_term_structure, "far_dte")

    def test_delta_neutral(self):
        """The one that was wrong: 0.50, not 0.30."""
        assert warmup._DEFAULT_DELTA == _query_default(routes.scanner_delta_neutral, "target_delta")
        assert warmup._DEFAULT_TARGET_DTE == _query_default(routes.scanner_delta_neutral, "target_dte")

    def test_gamma_exposure(self):
        assert warmup._DEFAULT_TARGET_DTE == _query_default(routes.scanner_gamma_exposure, "target_dte")


class TestEnabledFlag:
    @pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
    def test_truthy(self, monkeypatch, value):
        monkeypatch.setenv("WARM_CACHE", value)
        assert warmup.enabled() is True

    @pytest.mark.parametrize("value", ["", "0", "false", "off"])
    def test_falsy(self, monkeypatch, value):
        monkeypatch.setenv("WARM_CACHE", value)
        assert warmup.enabled() is False

    def test_off_by_default(self, monkeypatch):
        """Local development gains nothing (cold is sub-second there) and the
        test suite must not spawn threads against the committed database."""
        monkeypatch.delenv("WARM_CACHE", raising=False)
        assert warmup.enabled() is False

    def test_disabled_starts_no_thread(self, monkeypatch):
        monkeypatch.delenv("WARM_CACHE", raising=False)
        assert warmup.start_background_warm() is None


class TestWarmIsNeverFatal:
    def test_one_failing_symbol_does_not_abandon_the_rest(self, monkeypatch):
        """A thin chain must not cost the other 23 symbols their warm."""
        seen = []

        def boom(symbol, *args, **kwargs):
            seen.append(symbol)
            if symbol == "AAPL":
                raise RuntimeError("thin chain")
            return {}

        for name in ("_scanner_row", "_calendar_edge_row", "_term_structure_row",
                     "_delta_neutral_row", "_gamma_exposure_row", "_trade_idea"):
            monkeypatch.setattr(routes, name, boom)
        monkeypatch.setattr(routes, "_pcr_snapshot", lambda _e: {})

        warmup.warm()  # must not raise

        assert "AAPL" in seen
        assert len({s for s in seen}) > 1, "other symbols should still have been attempted"

    def test_pcr_failure_does_not_raise(self, monkeypatch):
        for name in ("_scanner_row", "_calendar_edge_row", "_term_structure_row",
                     "_delta_neutral_row", "_gamma_exposure_row", "_trade_idea"):
            monkeypatch.setattr(routes, name, lambda *a, **k: {})

        def boom(_expiration):
            raise RuntimeError("no chain")

        monkeypatch.setattr(routes, "_pcr_snapshot", boom)

        warmup.warm()  # must not raise
