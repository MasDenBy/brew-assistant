"""Eval runner: asks every question in eval_questions.csv, scores answers.

Keyword syntax (expected_keywords column):
- groups separated by ';': every group must match (AND)
- alternatives inside a group separated by '|': any one matches (OR)
- matching is case-insensitive substring
- "MANUAL": skipped by the runner; human fills pass_fail after review

Run:
    .venv\\Scripts\\python.exe tests\\run_eval.py
    .venv\\Scripts\\python.exe tests\\run_eval.py --retrieval-only  # no LLM

Numbered citations [N] refer to the Nth retrieved document (format_context
numbers the prompt's context blocks 1..k in retrieval order, and the UI's
sources panel shows the same order). The citation audit is console-only and
non-gating: every [N] in an answer is checked against the number of
documents retrieved for that turn. Verdicts: OK (in 1..k range), INVALID
(out of range — the model invented a citation). The audit also reports how
many of the retrieved documents were never cited (coverage). PASS/FAIL
scoring is unchanged.
"""
import argparse
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rag import ask, get_retrieved_sources  # noqa: E402

CSV_PATH = ROOT / "tests" / "eval_questions.csv"
EXCERPT_LEN = 350

# One marker may list several documents, e.g. "[2]" or "[1, 2]"; a marker
# never contains "[source: ...]" text, so bracketed words are ignored.
CITATION_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


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


def cited_numbers(answer: str) -> list[int]:
    """Cited document numbers in order of first appearance (no duplicates)."""
    numbers = []
    for match in CITATION_RE.finditer(answer):
        for part in match.group(1).split(","):
            n = int(part)
            if n not in numbers:
                numbers.append(n)
    return numbers


def audit_citations(answer: str, sources: list[dict]) -> list[tuple[int, str]]:
    """[(number, verdict)] for each cited number.

    verdict is 'OK' (1..k of this turn's retrieved docs) or 'INVALID'
    (out of range — an invented citation).
    """
    k = len(sources)
    return [(n, "OK" if 1 <= n <= k else "INVALID") for n in cited_numbers(answer)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the brewing eval questions.")
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help="print top-k (type, source) per question without calling the LLM",
    )
    args = parser.parse_args()

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if args.retrieval_only:
        for i, row in enumerate(rows, 1):
            print(f"[{i}/{len(rows)}] {row['question']}")
            for rank, src in enumerate(get_retrieved_sources(row["question"]), 1):
                print(f"    {rank}. ({src['type']}) {src['source']}")
        return

    results = []
    citation_totals = {"OK": 0, "INVALID": 0}
    for i, row in enumerate(rows, 1):
        q = row["question"]
        print(f"[{i}/{len(rows)}] {q}")
        answer, sources = ask(q)
        row["actual_excerpt"] = " ".join(answer.split())[:EXCERPT_LEN]
        verdict = score(answer, row["expected_keywords"])
        row["pass_fail"] = verdict if verdict else "MANUAL"
        results.append(verdict)
        print(f"    -> {row['pass_fail']}: {row['actual_excerpt'][:120]}...")

        findings = audit_citations(answer, sources)
        row_counts = dict.fromkeys(citation_totals, 0)
        for _, citation_verdict in findings:
            citation_totals[citation_verdict] += 1
            row_counts[citation_verdict] += 1
        printed = (
            "    citations: {OK} ok, {INVALID} invalid".format(**row_counts)
        )
        if sources:
            printed += f"; cited {len(findings)}/{len(sources)} retrieved docs"
        print(printed)
        for n, citation_verdict in findings:
            if citation_verdict != "OK":
                print(f"      INVALID: [{n}] (retrieved docs are 1..{len(sources)})")

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
    print("citations: {OK} ok, {INVALID} invalid".format(**citation_totals))
    if failed:
        print("FAILING QUESTIONS:")
        for row in rows:
            if row["pass_fail"] == "FAIL":
                print(f"  - {row['question']}")


if __name__ == "__main__":
    main()
