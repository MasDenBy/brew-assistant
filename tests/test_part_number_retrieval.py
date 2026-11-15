"""Unit tests for exact part-number retrieval (rag part-number helpers).

No ChromaDB, no embeddings, no LLM. Run:
    .venv\\Scripts\\python.exe tests\\test_part_number_retrieval.py

Background: embeddings carry no signal for opaque product codes ("OYL-042"),
so the similarity search ranks among sibling strains and the asked-for
strain can fall out of top-k (verified with the Omega catalog). retrieve()
pins exact matches; these tests cover the pure helpers behind it.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langchain_core.documents import Document  # noqa: E402

from rag import (  # noqa: E402
    MAX_PINNED_DOCS,
    _PART_TOKEN_RE,
    _norm_part,
    merge_pinned,
    part_number_docs,
)


def test_tokens():
    assert _PART_TOKEN_RE.findall("Tell me about a OYL-042 yeasts") == ["OYL-042"]
    assert _PART_TOKEN_RE.findall("What is the attenuation of WLP029?") == ["WLP029"]
    assert _PART_TOKEN_RE.findall("Wyeast 3068 temperature") == ["3068"]
    assert _PART_TOKEN_RE.findall("Wyeast 2035-PC availability") == ["2035-PC"]
    assert _PART_TOKEN_RE.findall("us-05 at 18C") == ["us-05"]
    assert _PART_TOKEN_RE.findall("PIN-001Dry vs OYL-071Dry") == ["PIN-001Dry", "OYL-071Dry"]
    # no product code -> no pinning
    assert _PART_TOKEN_RE.findall("cascade substitute") == []
    assert _PART_TOKEN_RE.findall("Tell me about Belgian saison styles") == []
    # CO2 has a single digit; ABV/OG are not part numbers
    assert _PART_TOKEN_RE.findall("How much CO2 for an ABV of 5%?") == []


def test_norm_part():
    assert _norm_part("OYL-042") == "OYL042"
    assert _norm_part("oyl 042") == "OYL042"
    assert _norm_part("OYL042") == "OYL042"
    assert _norm_part("2035-PC") == "2035PC"


def _doc(source: str, content: str, type_: str = "yeast") -> Document:
    return Document(page_content=content, metadata={"source": source, "type": type_})


def test_merge_pinned_pins_exact_match_first():
    sim = [_doc("omega/ingredients/yeast-oyl-001-alt.md", "OYL-001")]
    pinned = [_doc("omega/ingredients/yeast-oyl-042-belgian-saison-ii.md", "OYL-042")]
    merged = merge_pinned(sim, pinned, limit=8)
    assert merged[0].metadata["source"].endswith("oyl-042-belgian-saison-ii.md")
    assert len(merged) == 2  # similarity fill after the pinned doc


def test_merge_pinned_dedupes_by_source():
    pinned = [_doc("omega/ingredients/yeast-oyl-042-belgian-saison-ii.md", "OYL-042")]
    sim = [
        _doc("omega\\ingredients\\yeast-oyl-042-belgian-saison-ii.md", "dup"),
        _doc("omega/ingredients/yeast-oyl-001-alt.md", "other"),
    ]
    merged = merge_pinned(sim, pinned, limit=8)
    assert len(merged) == 2  # duplicate source dropped (windows path normalized)
    assert [d.page_content for d in merged] == ["OYL-042", "other"]


def test_merge_pinned_respects_limit():
    pinned = [_doc("a.md", "OYL-042")]
    sim = [_doc(f"s{i}.md", "x") for i in range(5)]
    merged = merge_pinned(sim, pinned, limit=3)
    assert len(merged) == 3
    assert merged[0].page_content == "OYL-042"


def test_merge_pinned_noop_without_pinned():
    sim = [_doc("a.md", "x")]
    assert merge_pinned(sim, [], limit=8) == sim


def test_part_number_docs_query_without_codes():
    assert part_number_docs("cascade substitute") == []


def test_max_pinned_cap():
    # more matching tokens than the cap -> truncated, never unlimited
    query = " ".join(f"OYL-{n:03d}" for n in range(1, 10))
    assert MAX_PINNED_DOCS == 4
    # (store-backed; the cap is enforced inside part_number_docs itself)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS: {t.__name__}")
    print(f"ALL {len(tests)} PASSED")
