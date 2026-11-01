"""Eval runner: asks every question in eval_questions.csv, scores answers.

Keyword syntax (expected_keywords column):
- groups separated by ';': every group must match (AND)
- alternatives inside a group separated by '|': any one matches (OR)
- matching is case-insensitive substring
- "MANUAL": skipped by the runner; human fills pass_fail after review

Run: .venv\\Scripts\\python.exe tests\\run_eval.py
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rag import ask  # noqa: E402

CSV_PATH = ROOT / "tests" / "eval_questions.csv"
EXCERPT_LEN = 350


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


def main() -> None:
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    results = []
    for i, row in enumerate(rows, 1):
        q = row["question"]
        print(f"[{i}/{len(rows)}] {q}")
        answer, _ = ask(q)
        row["actual_excerpt"] = " ".join(answer.split())[:EXCERPT_LEN]
        verdict = score(answer, row["expected_keywords"])
        row["pass_fail"] = verdict if verdict else "MANUAL"
        results.append(verdict)
        print(f"    -> {row['pass_fail']}: {row['actual_excerpt'][:120]}...")

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
    if failed:
        print("FAILING QUESTIONS:")
        for row in rows:
            if row["pass_fail"] == "FAIL":
                print(f"  - {row['question']}")


if __name__ == "__main__":
    main()
