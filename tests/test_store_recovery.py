"""Offline tests for stale-store recovery (rag._with_store_recovery).

When the collection under the app's cached chroma client is deleted and
re-created (re-ingest — UI button or a CLI run while the app is open),
cached handles go stale: queries fail with NotFoundError ("Collection ...
does not exist") or 'RustBindingsAPI' object has no attribute 'bindings'.
retrieve()/type_counts() must rebuild the client once and retry.
Run: .venv\\Scripts\\python.exe\\tests\\test_store_recovery.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langchain_core.documents import Document  # noqa: E402

import rag  # noqa: E402
from rag import _is_stale_store_error  # noqa: E402


def test_matches_seen_failures() -> None:
    # both exact messages observed in the wild
    assert _is_stale_store_error(
        AttributeError("'RustBindingsAPI' object has no attribute 'bindings'")
    )
    assert _is_stale_store_error(Exception(
        "Error getting collection: Collection [ab1a2039] does not exist."
    ))
    assert _is_stale_store_error(Exception("Database file is locked"))


def test_other_errors_are_not_stale() -> None:
    assert not _is_stale_store_error(AttributeError("no attribute 'foo'"))
    assert not _is_stale_store_error(ValueError("nope"))
    # an AttributeError unrelated to the Rust bindings
    assert not _is_stale_store_error(AttributeError("'Chroma' object has no attribute 'count'"))


def _patch_store(monkey: "FakeStore") -> dict:
    """Swap the cached store for a fake; return the reset-call counter."""
    counter = {"resets": 0}
    rag._get_vectorstore = lambda: monkey  # type: ignore[assignment]
    rag.reset_caches = lambda: counter.__setitem__("resets", counter["resets"] + 1)  # type: ignore[assignment]
    return counter


def _restore_store(original_get, original_reset) -> None:
    rag._get_vectorstore = original_get  # type: ignore[assignment]
    rag.reset_caches = original_reset  # type: ignore[assignment]
    # a dump cached under the fake would leak into later tests
    rag._store_dump.cache_clear()
    rag._part_index.cache_clear()
    rag._style_title_index.cache_clear()


class FakeStore:
    def __init__(self) -> None:
        self.calls = 0
        self.fail_first = True

    def similarity_search(self, *args, **kwargs) -> list[Document]:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise type(
                "NotFoundError", (Exception,), {}
            )("Error getting collection: Collection [ab1a2039] does not exist.")
        return [Document(page_content="ok", metadata={"source": "x.md"})]

    def get(self, include=None) -> dict:
        """Minimal store dump for the pin indexes (no style titles)."""
        return {"documents": ["# Fake doc"], "metadatas": [{"source": "fake.md"}]}


def test_retrieve_recovers_once() -> None:
    original_get, original_reset = rag._get_vectorstore, rag.reset_caches
    fake = FakeStore()
    counter = _patch_store(fake)
    try:
        docs = rag.retrieve("Tell me about English IPA.")
        assert fake.calls == 2, fake.calls          # failed once, retried once
        assert counter["resets"] == 1, counter      # rebuilt the client once
        assert len(docs) == 1 and docs[0].page_content == "ok"
    finally:
        _restore_store(original_get, original_reset)


def test_non_stale_error_propagates() -> None:
    original_get, original_reset = rag._get_vectorstore, rag.reset_caches
    counter = {"resets": 0}
    calls = {"n": 0}

    class Broken:
        def similarity_search(self, *args, **kwargs):
            calls["n"] += 1
            raise ValueError("not a stale error")

    rag._get_vectorstore = lambda: Broken()  # type: ignore[assignment]
    rag.reset_caches = lambda: counter.__setitem__("resets", counter["resets"] + 1)  # type: ignore[assignment]
    try:
        try:
            rag.retrieve("anything")
            raise AssertionError("ValueError should propagate")
        except ValueError:
            pass
        assert calls["n"] == 1, calls      # no retry
        assert counter["resets"] == 0, counter
    finally:
        _restore_store(original_get, original_reset)


def main() -> None:
    test_matches_seen_failures()
    print("PASS: detector matches both observed stale-store failures")
    test_other_errors_are_not_stale()
    print("PASS: unrelated errors are not treated as stale")
    test_retrieve_recovers_once()
    print("PASS: retrieve rebuilds the client once and retries")
    test_non_stale_error_propagates()
    print("PASS: non-stale errors propagate without retry")
    print("ALL 4 PASSED")


if __name__ == "__main__":
    main()
