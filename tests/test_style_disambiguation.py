"""Unit tests for the deterministic style disambiguation (rag.py).

Fake Documents only — no ChromaDB, no embeddings, no LLM. Run:
    .venv\\Scripts\\python.exe tests\\test_style_disambiguation.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langchain_core.documents import Document

from rag import (
    DISAMBIGUATION_QUESTION,
    _style_description,
    disambiguation_reply,
    style_disambiguation_matches,
)

BJCP_LIGHT = Document(
    page_content=(
        "# 1A American Light Lager (BJCP style)\n\n"
        "Category: Standard American Beer (BJCP2021)\n"
        "Type: lager (bottom-fermented)\n"
        "Tags: session-strength, pale-color\n\n"
        "## Vital statistics\n"
        "OG: 1.028 - 1.04 | FG: 0.998 - 1.008 | ABV: 2.8% - 4.2% | IBU: 8 - 12 | Color: 2 - 3 SRM\n\n"
        "## Characteristic ingredients\n"
        "Two- or six-row barley with up to 40% rice or corn as adjuncts. Lager yeast. Negligible hops.\n\n"
        "## Overall impression\n"
        "A highly carbonated, very light-bodied lager. Designed to be consumed very cold.\n\n"
        "## Aroma\n"
        "Low to none."
    ),
    metadata={
        "source": "bjcp/bjcp/1a-american-light-lager.md",
        "type": "bjcp_style",
        "title": "1A American Light Lager",
    },
)
BA_LIGHT = Document(
    page_content=(
        "# Contemporary American-Style Light Lager (BA style)\n\n"
        "Family: Lager Styles | Origin: North American Origin Lager Styles | "
        "BA style id: 874 | Source: Brewers Association Beer Style Guidelines (2026)\n\n"
        "## Vital statistics\n"
        "OG: 1.024-1.04 | FG: 0.992-1.008 | IBU: 5-20 | Color: 1.5-12\n\n"
        "## Color\n"
        "Very light to medium amber. The word Light refers to light body and reduced calories.\n\n"
        "## Body\n"
        "Low to medium-low."
    ),
    metadata={
        "source": "ba/styles/874-contemporary-american-style-light-lager.md",
        "type": "style",
        "title": "Contemporary American-Style Light Lager (BA style)",
    },
)
BA_AMERICAN_LAGER = Document(
    page_content=(
        "# American-Style Lager (BA style)\n\n"
        "Family: Lager Styles | Source: Brewers Association\n\n"
        "## Vital statistics\n"
        "OG: 1.040-1.048 | IBU: 8-18\n\n"
        "## Flavor\n"
        "A balanced, clean lager with a subtle malt character."
    ),
    metadata={
        "source": "ba/styles/826-american-style-lager.md",
        "type": "style",
        "title": "American-Style Lager (BA style)",
    },
)
YEAST_DOC = Document(
    page_content="Wyeast 2007 Pilsen Lager yeast.",
    metadata={"source": "wyeast/ingredients/yeast-2007-pilsen-lager.md", "type": "yeast"},
)
DOCS = [BA_LIGHT, YEAST_DOC, BA_AMERICAN_LAGER, BJCP_LIGHT]


def test_two_guidelines_match():
    matches = style_disambiguation_matches("Tell me about American Light Lager.", DOCS)
    ids = [d.metadata["source"] for _, d in matches]
    # BA light (index 1) and BJCP light (index 4); the plain American Lager
    # does NOT match because the question's "light" is not in its title.
    assert ids == [
        "ba/styles/874-contemporary-american-style-light-lager.md",
        "bjcp/bjcp/1a-american-light-lager.md",
    ], ids
    assert [i for i, _ in matches] == [1, 4]


def test_reply_shape():
    reply = disambiguation_reply("Tell me about American Light Lager.", DOCS)
    assert reply is not None
    lines = reply.splitlines()
    assert lines[0] == "Contemporary American-Style Light Lager (BA style) [1]"
    assert lines[3] == "1A American Light Lager (BJCP style) [4]"
    assert reply.endswith(DISAMBIGUATION_QUESTION)
    # Descriptions are pure prose: no stats ranges, no OG/IBU tokens.
    desc_lines = [lines[1], lines[4]]
    assert all("OG" not in l and "IBU" not in l and "|" not in l for l in desc_lines)
    assert "highly carbonated" in lines[4]  # BJCP overall impression used
    # Guideline suffix not duplicated when the title already has it.
    assert "(BA style) (BA style)" not in reply


def test_single_match_is_none():
    assert style_disambiguation_matches("Tell me about Mexican Lager.", DOCS) == []
    assert disambiguation_reply("Tell me about Mexican Lager.", DOCS) is None


def test_ingredient_question_is_none():
    # "hops" is not in any style title -> normal LLM flow, not disambiguation.
    assert (
        style_disambiguation_matches("What hops does American Light Lager use?", DOCS)
        == []
    )


def test_plural_question_token():
    # "lagers" singularizes to "lager"; both BA styles match the plain name.
    matches = style_disambiguation_matches("Tell me about American Lagers.", DOCS)
    sources = [d.metadata["source"] for _, d in matches]
    assert "ba/styles/826-american-style-lager.md" in sources
    assert "ba/styles/874-contemporary-american-style-light-lager.md" in sources
    reply = disambiguation_reply("Tell me about American Lagers.", DOCS)
    assert reply is not None and reply.count("\n\n") >= 2


def test_description_extraction():
    # Skips meta lines and vital statistics; takes first prose sentences.
    desc = _style_description(BA_LIGHT.page_content)
    assert "Family:" not in desc and "OG:" not in desc
    assert desc.startswith("Very light to medium amber.")
    assert _style_description(BJCP_LIGHT.page_content).startswith("A highly carbonated")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS: {t.__name__}")
    print(f"ALL {len(tests)} PASSED")
