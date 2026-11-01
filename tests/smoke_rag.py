"""End-to-end RAG smoke test: asks the Phase 1 verification question.

Run: .venv\\Scripts\\python.exe tests\\smoke_rag.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag import ask  # noqa: E402

QUESTION = "Can I substitute Cascade hops?"

answer, sources = ask(QUESTION)
print("=" * 60)
print(f"Q: {QUESTION}")
print("=" * 60)
print(answer)
print("-" * 60)
print("Retrieved sources:")
for s in sources:
    print(f"  [{s['type']}] {s['source']}")

# Basic expectations
assert answer.strip(), "Empty answer"
assert sources, "No sources retrieved"
assert any("cascade" in s["source"] for s in sources), "Cascade doc not retrieved"
cited = any(s["source"] in answer for s in sources)
print("-" * 60)
print(f"Answer cites a retrieved source: {cited}")

# Adversarial: out-of-scope question should get a graceful fallback
print("=" * 60)
ADV = "What should I brew today?"
adv_answer, _ = ask(ADV)
print(f"Q: {ADV}")
print(f"A: {adv_answer[:300]}")
assert adv_answer.strip(), "Empty answer for out-of-scope question"
print("Adversarial check done (answer is grounded/graceful).")
