"""Eval runner: asks every question in eval_questions.csv, scores answers.

Keyword syntax (expected_keywords column):
- groups separated by ';': every group must match (AND)
- alternatives inside a group separated by '|': any one matches (OR)
- matching is case-insensitive substring
- "MANUAL": skipped by the runner; human fills pass_fail after review

Run:
    .venv\\Scripts\\python.exe tests\\run_eval.py
    .venv\\Scripts\\python.exe tests\\run_eval.py --retrieval-only  # no LLM

The citation audit is console-only and non-gating: every `[source: ...]` in an
answer is checked against `data_dirs` on disk and against the documents
retrieved for that turn. Verdicts: OK, ABBREVIATED (missing on disk but a
unique suffix of a retrieved path, e.g. `ingredients/x.md` for
`whitelabs/ingredients/x.md`), PHANTOM (not on disk and not a retrieved doc —
a fabricated path), UNGROUNDED (on disk but not retrieved this turn).
PASS/FAIL scoring is unchanged.
"""
import argparse
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import load_config  # noqa: E402
from rag import ask, get_retrieved_sources  # noqa: E402

CSV_PATH = ROOT / "tests" / "eval_questions.csv"
EXCERPT_LEN = 350

# Models sometimes append a markdown link after the citation, e.g.
# "[source: a.md](source: a.md)" — the first `]` ends the capture, so the
# following markdown never leaks into the path.
CITATION_RE = re.compile(r"\[source:\s*([^\]]+)\]")


def score(answer: str, expected: str) -> str | None:
    """Return 'PASS'/'FAIL', or None for MANUAL rows."""
    expected = expected.strip()
    if not expected or expected.upper() == "MANUAL":
        return None
    text = answer.lower()
    for group in expected.split(";"):
        alternatives = [a.strip().lower() for a in group.split("|") if a.strip()]
        if alternatives and not any(a in text for a in alternatives):
            return "FAIL"
    return "PASS"


def _norm_path(path: str) -> str:
    """POSIX-style, no surrounding markdown/backticks — matches rag sources.

    Model aberrations are cut down to the first path token: a bare
    ``[source: a/b.md (source: a/b.md)]`` capture yields ``a/b.md``.
    """
    path = re.sub(r"^\s*source:\s*", "", path, flags=re.IGNORECASE).strip()
    if not path:
        return ""
    path = path.split()[0]
    return path.strip("`'\"*[]()").replace("\\", "/")


def cited_paths(answer: str) -> list[str]:
    """Citation paths in order of first appearance (one bracket may list many)."""
    paths = []
    for match in CITATION_RE.finditer(answer):
        for part in match.group(1).split(","):
            path = _norm_path(part)
            if path and path not in paths:
                paths.append(path)
    return paths


def _on_disk(path: str, data_dirs: list[Path]) -> bool:
    """True if the cited path exists under any configured data dir."""
    return any((base / path).is_file() for base in data_dirs)


def audit_citations(
    answer: str, sources: list[dict], data_dirs: list[Path]
) -> list[tuple[str, str, str | None]]:
    """[(path, verdict, resolved)] for each cited path.

    verdict is 'OK' | 'ABBREVIATED' | 'PHANTOM' | 'UNGROUNDED'; `resolved` is
    the retrieved source an ABBREVIATED path stands for, else None.
    """
    retrieved = {_norm_path(s["source"]) for s in sources}
    findings = []
    for path in cited_paths(answer):
        resolved = None
        if not _on_disk(path, data_dirs):
            matches = [r for r in retrieved if r.endswith("/" + path)]
            if len(matches) == 1:
                verdict, resolved = "ABBREVIATED", matches[0]
            else:
                verdict = "PHANTOM"
        elif path not in retrieved:
            verdict = "UNGROUNDED"
        else:
            verdict = "OK"
        findings.append((path, verdict, resolved))
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the brewing eval questions.")
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help="print top-k (type, source) per question without calling the LLM",
    )
    args = parser.parse_args()

    cfg = load_config()
    data_dirs = cfg.get("data_dirs") or [cfg.get("data_dir", ROOT / "data")]

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if args.retrieval_only:
        for i, row in enumerate(rows, 1):
            print(f"[{i}/{len(rows)}] {row['question']}")
            for rank, src in enumerate(get_retrieved_sources(row["question"]), 1):
                print(f"    {rank}. ({src['type']}) {src['source']}")
        return

    results = []
    citation_totals = {"OK": 0, "ABBREVIATED": 0, "PHANTOM": 0, "UNGROUNDED": 0}
    for i, row in enumerate(rows, 1):
        q = row["question"]
        print(f"[{i}/{len(rows)}] {q}")
        answer, sources = ask(q)
        row["actual_excerpt"] = " ".join(answer.split())[:EXCERPT_LEN]
        verdict = score(answer, row["expected_keywords"])
        row["pass_fail"] = verdict if verdict else "MANUAL"
        results.append(verdict)
        print(f"    -> {row['pass_fail']}: {row['actual_excerpt'][:120]}...")

        findings = audit_citations(answer, sources, data_dirs)
        row_counts = dict.fromkeys(citation_totals, 0)
        for path, citation_verdict, resolved in findings:
            citation_totals[citation_verdict] += 1
            row_counts[citation_verdict] += 1
        print(
            "    citations: {OK} ok, {ABBREVIATED} abbreviated, "
            "{PHANTOM} phantom, {UNGROUNDED} ungrounded".format(**row_counts)
        )
        for path, citation_verdict, resolved in findings:
            if citation_verdict == "ABBREVIATED":
                print(f"      ABBREVIATED: {path} -> {resolved}")
            elif citation_verdict != "OK":
                print(f"      {citation_verdict}: {path}")

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["question", "expected_keywords", "actual_excerpt", "pass_fail", "notes"],
        )
        writer.writeheader()
        writer.writerows(rows)

    passed = results.count("PASS")
    failed = results.count("FAIL")
    manual = results.count(None)
    print("-" * 60)
    print(f"TOTAL: {passed} pass, {failed} fail, {manual} manual review")
    print(
        "citations: {OK} ok, {ABBREVIATED} abbreviated, {PHANTOM} phantom, "
        "{UNGROUNDED} ungrounded".format(**citation_totals)
    )
    if failed:
        print("FAILING QUESTIONS:")
        for row in rows:
            if row["pass_fail"] == "FAIL":
                print(f"  - {row['question']}")


if __name__ == "__main__":
    main()
