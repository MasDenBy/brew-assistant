"""Unit tests for citation post-processing (rag.normalize_citations).

No ChromaDB, no embeddings, no LLM. Run:
    .venv\\Scripts\\python.exe tests\\test_citations.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rag import normalize_citations  # noqa: E402


def test_adjacent_citations_collapse():
    text = "American IPA is hoppy [2] [3]."
    assert normalize_citations(text) == "American IPA is hoppy [2, 3]."


def test_repeated_citation_removed_in_same_paragraph():
    text = "The ABV is 5% [1]. It uses Cascade [1]."
    assert normalize_citations(text) == "The ABV is 5% [1]. It uses Cascade."


def test_citations_preserved_across_paragraphs():
    text = "Para one [1].\n\nPara two [1]."
    assert normalize_citations(text) == "Para one [1].\n\nPara two [1]."


def test_refusal_loses_all_citations():
    text = "I don't have that information. [1] does not provide the answer [2]."
    assert normalize_citations(text) == "I don't have that information.  does not provide the answer ."


def test_normal_answer_with_sparse_citations():
    text = "American IPA is a hoppy, moderately strong ale [1]. It showcases citrus, floral, or resinous American hop varieties [1]."
    assert normalize_citations(text) == (
        "American IPA is a hoppy, moderately strong ale [1]. "
        "It showcases citrus, floral, or resinous American hop varieties."
    )


def test_mixed_adjacent_and_duplicate():
    text = "A [1] [1]. B [2] [3]. C [1]."
    assert normalize_citations(text) == "A [1]. B [2, 3]. C."


def test_preserves_first_appearance_order():
    text = "A [3]. B [1]. C [3, 1]."
    assert normalize_citations(text) == "A [3]. B [1]. C."


def test_multi_number_citation_collapse():
    text = "Styles: [1, 2] [3]."
    assert normalize_citations(text) == "Styles: [1, 2, 3]."


def test_no_citations_unchanged():
    text = "Just a plain sentence."
    assert normalize_citations(text) == text


def test_list_block_deduplicates():
    text = "Hops:\n- Cascade [1]\n- Centennial [1]\n- Citra [2]"
    assert normalize_citations(text) == "Hops:\n- Cascade [1]\n- Centennial\n- Citra [2]"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS: {t.__name__}")
    print(f"ALL {len(tests)} PASSED")
