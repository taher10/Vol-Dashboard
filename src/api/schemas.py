"""
src/api/schemas.py

Pydantic response models for the parts of the API that aren't just a
serialized DataFrame (symbol registry, history stats). The DataFrame-shaped
payloads (chain-derived records, scored contracts, expiry scores) are
intentionally left as plain dict/list JSON — modeling every metrics column
in Pydantic would just duplicate src/metrics.py's/decision_engine.py's
column names a second time for no validation benefit, since this API only
ever reads data that those modules already produced and shaped.
"""

from __future__ import annotations

from pydantic import BaseModel


class SymbolOut(BaseModel):
    symbol: str
    color: str


class IVRankOut(BaseModel):
    current_iv: float
    iv_rank: float
    iv_percentile: float
    lookback_low: float
    lookback_high: float
    n_observations: int


class ZScoreOut(BaseModel):
    current_value: float
    trailing_mean: float
    trailing_std: float
    zscore: float
    n_observations: int
