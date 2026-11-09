"""Retrieval + search-scope checks (offline: embeddings + Chroma only).

Run: .venv\\Scripts\\python.exe tests\\smoke_retrieval.py
Skips cleanly (exit 0) when the store has not been ingested yet.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chromadb  # noqa: E402

import rag  # noqa: E402
from config import load_config  # noqa: E402

K = 6
QUERY = "cascade substitute"


def open_store_count(cfg: dict) -> tuple[int, str | None]:
    """Chunk count in the collection, or (0, reason) when it is not there."""
    path = Path(cfg["chroma_dir"])
    if not path.exists():
        return 0, "chroma_dir missing"
    client = chromadb.PersistentClient(path=str(path))
    try:
        collection = client.get_collection(rag.COLLECTION_NAME)
    except Exception as exc:  # missing collection: Nothing to test yet
        return 0, f"{rag.COLLECTION_NAME} missing ({type(exc).__name__})"
    return collection.count(), None


def main() -> int:
    cfg = load_config()
    total, reason = open_store_count(cfg)
    if not total:
        print(f"SKIP: store not ingested ({reason or 'empty collection'})")
        return 0

    # ---- type inventory ----
    counts = rag.type_counts()
    assert counts, "type_counts() is empty for a non-empty store"
    assert all(isinstance(t, str) and t for t in counts), counts
    assert all(isinstance(n, int) and n > 0 for n in counts.values()), counts
    assert sum(counts.values()) == total, (sum(counts.values()), total)

    options = rag.scope_options()
    assert options, "scope_options() is empty"
    assert len(options) == len(set(options)), f"duplicate scope options: {options}"
    assert set(options) == set(counts), (options, counts)
    known = [t for t in rag.TYPE_ORDER if t in counts]
    assert options[: len(known)] == known, f"TYPE_ORDER prefix broken: {options}"

    print(f"{'type':<12} {'count':>6}")
    print("-" * 20)
    for t in options:
        print(f"{t:<12} {counts[t]:>6}")
    print("-" * 20)
    print(f"{'TOTAL':<12} {sum(counts.values()):>6}")

    # ---- retrieval without a filter ----
    all_docs = rag.retrieve(QUERY, k=K)
    assert len(all_docs) == K, f"unfiltered k={K} returned {len(all_docs)}"

    # ---- single type ----
    hops = rag.retrieve(QUERY, k=K, types=["hop"])
    assert hops, "types=['hop'] returned nothing"
    assert all(d.metadata.get("type") == "hop" for d in hops), [d.metadata for d in hops]

    # ---- two types ----
    pair = rag.retrieve(QUERY, k=K, types=["hop", "malt"])
    assert pair, "types=['hop', 'malt'] returned nothing"
    assert {d.metadata.get("type") for d in pair} <= {"hop", "malt"}, [
        d.metadata for d in pair
    ]

    # ---- another single type ----
    bjcp = rag.retrieve("vital stats for American IPA", k=K, types=["bjcp_style"])
    assert bjcp, "types=['bjcp_style'] returned nothing"
    assert all(d.metadata.get("type") == "bjcp_style" for d in bjcp), [
        d.metadata for d in bjcp
    ]

    # ---- unknown type: empty, no exception ----
    assert rag.retrieve(QUERY, k=K, types=["no_such_type"]) == []

    # ---- empty scope means "all types" ----
    empty_scope = rag.retrieve(QUERY, k=K, types=[])
    assert len(empty_scope) == K, f"empty scope k={K} returned {len(empty_scope)}"
    assert [d.page_content for d in empty_scope] == [d.page_content for d in all_docs]

    # ---- source dicts ----
    sources = rag.docs_to_sources(all_docs)
    assert len(sources) == len(all_docs), "docs_to_sources changed the doc count"
    for s in sources:
        assert s["source"], f"missing source: {s}"
        assert s["type"], f"missing type: {s}"
        assert s["title"], f"missing title: {s}"
        assert s["content"], f"missing content: {s}"

    # ---- back-compat wrapper threads the filter ----
    scoped = rag.get_retrieved_sources(QUERY, k=2, types=["hop"])
    assert 0 < len(scoped) <= 2, scoped
    assert all(s["type"] == "hop" for s in scoped), scoped

    print(
        f"PASS: {len(options)} types, {total} chunks, "
        "filter + scope helpers + source dicts OK"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
