"""
src — SPX Options Volatility Database

Classes
-------
SchwabAuth      src.auth             OAuth credential management
OptionsFetcher  src.options_fetcher  Fetch chains and price history
CSVStore        src.data_store       Read/write CSV snapshots
VolatilityMetrics  src.metrics       Compute vol surface metrics
OptionsVolJob   src.job              Full pipeline orchestrator

These are resolved on first attribute access rather than imported here.
Importing them eagerly made `schwab-py` a hard requirement of touching
*anything* under `src` -- including `src.api`, which on the demo deployment
serves a frozen database and must never be able to authenticate. The read
path has no use for the Schwab SDK, and a box that cannot import it is a
stronger guarantee than a flag that merely removes the routes.

`from src import SchwabAuth` still works; it just pays for the SDK at that
moment instead of at interpreter start.
"""

from importlib import import_module
from typing import TYPE_CHECKING

_LAZY = {
    "SchwabAuth": ".auth",
    "CSVStore": ".data_store",
    "ParquetStore": ".data_store",
    "VolatilityMetrics": ".metrics",
    "OptionsFetcher": ".options_fetcher",
    "OptionsVolJob": ".job",
}

__all__ = sorted(_LAZY)


def __getattr__(name: str):
    """PEP 562 module-level attribute hook -- see the module docstring."""
    if name in _LAZY:
        return getattr(import_module(_LAZY[name], __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY})


if TYPE_CHECKING:  # keeps editors/type-checkers aware of the real names
    from .auth import SchwabAuth
    from .data_store import CSVStore, ParquetStore
    from .job import OptionsVolJob
    from .metrics import VolatilityMetrics
    from .options_fetcher import OptionsFetcher
