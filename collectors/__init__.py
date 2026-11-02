"""Brew Assistant data collector application (see PHASE_2_PLAN.md §3).

Collectors fetch brewing documents from external sources and write them —
in canonical RAG format — under ``collect_dest/<id>/``. Raw/third-party and
personal data never enter the git repository.

Usage::

    python -m collectors                      # all collectors from config.yaml
    python -m collectors --only bjcp,diydog   # just these
    python -m collectors --dest D:\\brew      # override collect_dest
"""
from __future__ import annotations

import importlib
import pkgutil

from .base import (
    CollectorError,
    DataCollector,
    SourceUnavailableError,
    all_registered,
    get_collector,
    register,
)


def discover() -> None:
    """Import every ``*_collector`` module in this package so @register runs.

    Called by the runner and tests; safe to call repeatedly (already-imported
    modules are skipped by Python's import machinery).
    """
    for module in pkgutil.iter_modules(__path__):
        if module.name.endswith("_collector"):
            importlib.import_module(f"{__name__}.{module.name}")


__all__ = [
    "CollectorError",
    "DataCollector",
    "SourceUnavailableError",
    "all_registered",
    "discover",
    "get_collector",
    "register",
]
