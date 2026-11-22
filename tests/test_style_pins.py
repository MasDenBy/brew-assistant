"""Offline tests for style-title pinning (rag.style_title_docs).

'Tell me about English IPA.' retrieved 8/8 recipe chunks — recipe style
lines ("Style: English IPA") crowd the BJCP style guideline out of top-k.
Style docs whose TITLE covers every content token of the query are now
pinned deterministically (same rule as style disambiguation, but over the
whole store). Run: .venv\\Scripts\\python.exe tests\\test_style_pins.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langchain_core.documents import Document  # noqa: E402

import rag  # noqa: E402
from rag import (  # noqa: E402
    MAX_STYLE_PINS,
    style_title_docs,
)


def _dump(docs: list[Document]):
    documents = tuple(d.page_content for d in docs)
    metadatas = tuple(d.metadata for d in docs)
    return documents, metadatas


def _patch_store(monkey_docs: list[Document]) -> None:
    rag._store_dump.cache_clear()
    rag._style_title_index.cache_clear()
    original = rag._store_dump
    rag._store_dump = lambda: _dump(monkey_docs)  # type: ignore[assignment]
    rag._style_title_index.cache_clear()
    # rebuild the index lazily via the patched dump: clear the cached
    # function itself so the next call re-executes against the patched dump
    rag._style_title_index = rag.lru_cache(maxsize=1)(
        lambda: _build_index(monkey_docs)
    )


def _build_index(docs: list[Document]):
    entries = []
    for i, d in enumerate(docs):
        if d.metadata.get("type") not in rag.STYLE_TYPES:
            continue
        tokens = rag._tokens(d.metadata.get("title", ""))
        if tokens:
            entries.append((tokens, i))
    return tuple(entries)


def _restore_store() -> None:
    rag._store_dump = original_store_dump
    rag._style_title_index = original_style_index
    rag._style_title_index.cache_clear()


original_store_dump = rag._store_dump
original_style_index = rag._style_title_index


def _doc(source: str, title: str, type_: str = "style", content: str = "prose") -> Document:
    return Document(page_content=content, metadata={"source": source, "title": title, "type": type_})


FAKE_STORE = [
    _doc("recipes/old_world_india_pale_ale.xml", "Old World India Pale Ale (recipe)", type_="recipe"),
    _doc("recipes/atlantic_ipa_ale.xml", "Atlantic IPA (recipe)", type_="recipe"),
    _doc("bjcp/bjcp/11c-english-ipa.md", "11C. English IPA", type_="bjcp_style"),
    _doc("ba/styles/english-style-india-pale-ale.md", "English-Style India Pale Ale", type_="style"),
    _doc("whitelabs/ingredients/yeast-wlp009-english-ale.md", "WLP009 English Ale Yeast", type_="yeast"),
]


def test_english_ipa_pins_bjcp_style():
    _patch_store(FAKE_STORE)
    try:
        pinned = style_title_docs("Tell me about English IPA.")
        sources = [d.metadata["source"] for d in pinned]
        assert sources == ["bjcp/bjcp/11c-english-ipa.md"], sources
    finally:
        _restore_store()


def test_partial_token_match_does_not_pin():
    # 'hops'/'yeast' questions must not pin a style doc whose title lacks
    # the token — every question token must be covered by the title
    _patch_store(FAKE_STORE)
    try:
        assert style_title_docs("Which yeasts might be good for English IPA?") == []
        assert style_title_docs("What hops are typical in English IPA?") == []
    finally:
        _restore_store()


def test_multi_guideline_styles_all_pin():
    # 'American Light Lager' matches both guidelines -> both pinned, in
    # store order, so the deterministic disambiguation reply fires
    store = FAKE_STORE + [
        _doc("bjcp/bjcp/1a-american-light-lager.md", "1A American Light Lager", type_="bjcp_style"),
        _doc("ba/styles/contemporary-american-style-light-lager.md",
             "Contemporary American-Style Light Lager", type_="style"),
    ]
    _patch_store(store)
    try:
        pinned = style_title_docs("Tell me about American Light Lager.")
        sources = [d.metadata["source"] for d in pinned]
        assert sources == [
            "bjcp/bjcp/1a-american-light-lager.md",
            "ba/styles/contemporary-american-style-light-lager.md",
        ], sources
    finally:
        _restore_store()


def test_generic_token_pins_capped():
    store = [
        _doc(f"ba/styles/saison-{i}.md", f"BA Saison Variant {i}", type_="style")
        for i in range(10)
    ]
    _patch_store(store)
    try:
        pinned = style_title_docs("Tell me about Saison.")
        assert len(pinned) == MAX_STYLE_PINS
    finally:
        _restore_store()


def test_no_style_tokens_no_pins():
    _patch_store(FAKE_STORE)
    try:
        # stopwords and part codes produce no style-title pins
        assert style_title_docs("What is the attenuation of WLP029?") == []
        assert style_title_docs("the the") == []
    finally:
        _restore_store()


def main() -> None:
    test_english_ipa_pins_bjcp_style()
    print("PASS: 'English IPA' question pins the BJCP style doc")
    test_partial_token_match_does_not_pin()
    print("PASS: partial token coverage never pins (yeast/hops questions)")
    test_multi_guideline_styles_all_pin()
    print("PASS: multi-guideline matches pin both docs (disambiguation fires)")
    test_generic_token_pins_capped()
    print("PASS: generic token pins capped at MAX_STYLE_PINS")
    test_no_style_tokens_no_pins()
    print("PASS: no style tokens -> no pins")
    print("ALL 5 PASSED")


if __name__ == "__main__":
    main()
