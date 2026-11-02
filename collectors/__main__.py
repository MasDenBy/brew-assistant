"""CLI entry point: ``python -m collectors``.

Examples::

    python -m collectors                      # all collectors from config.yaml
    python -m collectors --only bjcp,diydog   # just these two
    python -m collectors --dest D:\\brew      # override collect_dest

Exit code: 1 only if a collector FAILED (skipped/missing sources are
warnings — the source may simply be unreachable right now).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from config import load_config

from .runner import FAIL, MISSING, OK, SKIP, Result, run


def _print(results: list[Result]) -> None:
    width = max((len(r.collector_id) for r in results), default=0)
    for r in results:
        print(f"  [{r.status:7}] {r.collector_id:<{width}}  {r.detail}")
    counts = {s: 0 for s in (OK, SKIP, FAIL, MISSING)}
    for r in results:
        counts[r.status] += 1
    print(
        f"Summary: {counts[OK]} ok, {counts[SKIP]} skipped, "
        f"{counts[FAIL]} failed, {counts[MISSING]} missing"
    )
    if counts[MISSING]:
        print(
            "  'missing' = configured in config.yaml but no collector module "
            "exists yet (fine while steps are in progress)."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m collectors",
        description="Run brewing data collectors into collect_dest/<id>/.",
    )
    parser.add_argument(
        "--only",
        help="comma-separated collector ids (default: `collectors` in config.yaml)",
    )
    parser.add_argument(
        "--dest",
        help="destination root (default: `collect_dest` in config.yaml)",
    )
    args = parser.parse_args(argv)

    cfg = load_config()

    only = (
        [s.strip() for s in args.only.split(",") if s.strip()]
        if args.only
        else None
    )
    if only:
        ids = only
    else:
        ids = list(cfg.get("collectors") or [])
        if not ids:
            print("No collectors configured (`collectors` in config.yaml) and no --only given.")
            return 0

    dest_root = Path(args.dest).expanduser() if args.dest else Path(cfg["collect_dest"])
    dest_root.mkdir(parents=True, exist_ok=True)

    print(f"Collectors destination: {dest_root}")
    results = run(ids, dest_root, only=only)
    _print(results)

    return 1 if any(r.status == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
