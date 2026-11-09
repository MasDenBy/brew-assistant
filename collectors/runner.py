"""Run registered collectors against a destination root (pure, testable)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import discover
from .base import SourceUnavailableError, get_collector

# statuses reported by the runner
OK = "OK"          # collector ran and wrote files (0 is still OK — empty source)
SKIP = "SKIP"      # source unavailable (fail-soft: network down, page moved)
FAIL = "FAIL"      # unexpected error inside the collector
MISSING = "MISSING"  # id configured but no registered collector (not implemented yet)


@dataclass
class Result:
    collector_id: str
    status: str
    detail: str = ""
    files: int = 0


def run(
    collector_ids: list[str],
    dest_root: Path,
    only: list[str] | None = None,
) -> list[Result]:
    """Run collectors in order; each owns ``dest_root/<id>/``.

    ``collector_ids`` comes from config (the enabled set); ``only`` restricts
    it to a user-selected subset. Never raises for a single collector's
    failure — every outcome is a :class:`Result`.
    """
    discover()
    ids = list(only) if only is not None else list(collector_ids)
    results: list[Result] = []

    for cid in ids:
        try:
            collector = get_collector(cid)
        except KeyError:
            results.append(
                Result(
                    cid,
                    MISSING,
                    "not registered - collector module not implemented yet? "
                    "(see collectors/base.py: 'How to add a new data source')",
                )
            )
            continue
        dest = dest_root / cid  # namespace rule: collector owns only its subdir
        try:
            written = collector.collect(dest)
            results.append(Result(cid, OK, f"{len(written)} file(s)", len(written)))
        except SourceUnavailableError as exc:
            results.append(Result(cid, SKIP, str(exc)))
        except Exception as exc:  # noqa: BLE001 — runner must survive any collector
            results.append(Result(cid, FAIL, f"{type(exc).__name__}: {exc}"))

    return results
