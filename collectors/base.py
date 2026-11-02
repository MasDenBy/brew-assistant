"""Data collector framework: one collector class per source.

A collector fetches brewing documents from one resource (website, archive,
folder), normalizes them into a canonical RAG format, and writes them into
its own namespace directory::

    <collect_dest>/<collector.id>/
        recipes/*.xml        BeerXML recipes
        bjcp/*.md            BJCP style guidelines (one style per file)
        tables/*.csv         reference tables
        ingredients/*.md     hop/malt/yeast docs (prefix hop-/malt-/yeast-)
        styles/*.md          beer style docs

This is the **collector <-> ingest contract**: `ingest.py` only ever reads
files in these formats and knows nothing about collectors.

How to add a new data source
----------------------------
1. Create ``collectors/<name>_collector.py``.
2. Subclass :class:`DataCollector`; set class attributes ``id`` and
   ``description``.
3. Implement ``collect(self, dest) -> list[Path]`` — ``dest`` is already the
   namespaced directory you own (``collect_dest/<id>/``).
4. Decorate the class with :func:`@register`.
5. Add ``id`` to the ``collectors:`` list in ``config.yaml``.

Nothing in ``ingest.py`` / ``rag.py`` / ``app.py`` changes for a new source.

Rules for ``collect()``
-----------------------
- **Idempotent:** safe to re-run; clear your own subtree first (never touch
  other collectors' subtrees or files the user placed manually).
- **Fail soft:** raise :class:`SourceUnavailableError` when the source is
  unreachable (network down, page moved) — the runner reports SKIP.
- Stay inside ``dest``; never write outside your namespace.
- Never write raw HTML: normalize to markdown/text (see ``web.py``).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

_REGISTRY: dict[str, type["DataCollector"]] = {}


class CollectorError(Exception):
    """Unexpected collector failure — the runner reports this as FAIL."""


class SourceUnavailableError(CollectorError):
    """Source unreachable/missing — the runner reports this as SKIP (fail-soft)."""


class DataCollector(ABC):
    """Base class for a single data source. See module docstring for rules."""

    #: unique registry key, also the namespace directory under collect_dest
    id: str = ""
    #: one-line human description shown by the runner
    description: str = ""

    @abstractmethod
    def collect(self, dest: Path) -> list[Path]:
        """Fetch/normalize the source and write canonical files under ``dest``.

        Returns the list of files written. Must be idempotent and fail soft
        (:class:`SourceUnavailableError` for unreachable sources).
        """

    def __repr__(self) -> str:
        return f"<{type(self).__name__} id={self.id!r}>"


def register(cls: type[DataCollector]) -> type[DataCollector]:
    """Class decorator: add a collector to the registry (keyed by ``id``)."""
    if not cls.id:
        raise ValueError(f"{cls.__name__} must set a non-empty `id`")
    existing = _REGISTRY.get(cls.id)
    if existing is not None and existing is not cls:
        raise ValueError(
            f"duplicate collector id {cls.id!r}: {existing.__name__} vs {cls.__name__}"
        )
    _REGISTRY[cls.id] = cls
    return cls


def get_collector(collector_id: str) -> DataCollector:
    """Instantiate a registered collector by id (KeyError if unknown)."""
    return _REGISTRY[collector_id]()


def all_registered() -> dict[str, type[DataCollector]]:
    """Copy of the registry: {id: class}."""
    return dict(_REGISTRY)
