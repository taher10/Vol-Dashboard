"""
Tests for precomputed metrics (src/schwab_database.py + data_loader).

Why these exist
---------------
The public demo runs on a free-tier CPU roughly 30x slower than a laptop.
VolatilityMetrics.compute_all() costs ~150ms per symbol locally and ~5s
there, so a 24-symbol leaderboard took 237 SECONDS on its first request --
and the instance sleeps after 15 minutes, so every visitor after a quiet
spell paid it again. Storing the metrics at build time turns that into a
read.

Two properties have to hold or this is worse than the slowness it fixes:

  * Stored metrics must equal computed ones. A silently different number is
    far more damaging than a slow page.
  * An absent table must fall through to computing. Only a demo build fills
    it; the live dashboard recomputes on purpose, because deriving metrics
    from the same rows being served is what makes a chain/metrics date
    mismatch impossible.

The datetime round-trip is pinned specifically: orient="split" alone sends
a datetime64 column back as int64 epoch-milliseconds, which made
`expiration` render as 1792108800000 instead of a date.
"""

from datetime import date

import pandas as pd
import pytest

from src.dashboard import data_loader
from src.schwab_database import SchwabDatabase


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHWAB_DB_PATH", str(tmp_path / "chains.db"))
    data_loader._cached_bundle.cache_clear()
    yield SchwabDatabase()
    data_loader._cached_bundle.cache_clear()


def _frame() -> pd.DataFrame:
    return pd.DataFrame({
        "expiration": pd.to_datetime(["2026-10-16", "2026-11-20"]),
        "dte": [2, 37],
        "atm_iv": [27.049, 24.5],
    })


class TestRoundTrip:
    def test_values_survive_exactly(self, db):
        original = _frame()

        db.write_precomputed_metrics("AAPL", date(2026, 10, 15), {"term_structure": original})
        restored = db.precomputed_metrics("AAPL", date(2026, 10, 15))["term_structure"]

        pd.testing.assert_frame_equal(original, restored, check_dtype=False)

    def test_datetime_columns_stay_datetimes(self, db):
        """The specific regression: without this, `expiration` comes back as
        1792108800000 and every date on the page renders as a big number."""
        db.write_precomputed_metrics("AAPL", date(2026, 10, 15), {"term_structure": _frame()})

        restored = db.precomputed_metrics("AAPL", date(2026, 10, 15))["term_structure"]

        assert pd.api.types.is_datetime64_any_dtype(restored["expiration"])
        assert restored["expiration"].iloc[0] == pd.Timestamp("2026-10-16")

    def test_floats_are_not_rounded(self, db):
        db.write_precomputed_metrics("AAPL", date(2026, 10, 15), {"term_structure": _frame()})

        restored = db.precomputed_metrics("AAPL", date(2026, 10, 15))["term_structure"]

        assert restored["atm_iv"].iloc[0] == pytest.approx(27.049, abs=1e-12)

    def test_rewriting_replaces_rather_than_duplicates(self, db):
        db.write_precomputed_metrics("AAPL", date(2026, 10, 15), {"term_structure": _frame()})
        updated = _frame()
        updated.loc[0, "atm_iv"] = 99.0

        db.write_precomputed_metrics("AAPL", date(2026, 10, 15), {"term_structure": updated})

        restored = db.precomputed_metrics("AAPL", date(2026, 10, 15))["term_structure"]
        assert len(restored) == 2
        assert restored["atm_iv"].iloc[0] == 99.0


class TestAbsenceFallsThrough:
    def test_missing_rows_return_empty_dict(self, db):
        """Absent is the normal case, not an error -- the live dashboard has
        no precomputed table content at all."""
        assert db.precomputed_metrics("AAPL", date(2026, 10, 15)) == {}

    def test_empty_frames_are_not_stored(self, db):
        """Storing an empty frame would make load_latest_snapshot treat it as
        'precomputed' and skip computing, serving nothing."""
        written = db.write_precomputed_metrics(
            "AAPL", date(2026, 10, 15), {"term_structure": pd.DataFrame()}
        )

        assert written == 0
        assert db.precomputed_metrics("AAPL", date(2026, 10, 15)) == {}

    def test_other_symbols_and_dates_are_isolated(self, db):
        db.write_precomputed_metrics("AAPL", date(2026, 10, 15), {"term_structure": _frame()})

        assert db.precomputed_metrics("NVDA", date(2026, 10, 15)) == {}
        assert db.precomputed_metrics("AAPL", date(2026, 10, 14)) == {}
