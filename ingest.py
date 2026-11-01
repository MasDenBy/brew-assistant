"""Ingestion: load brewing docs from data/, chunk, embed, store in ChromaDB.

Run: python ingest.py
Idempotent: re-running drops and rebuilds the collection.
"""
from pathlib import Path
import sys

import chromadb
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import load_config

COLLECTION_NAME = "brew_docs"
# Docs are entity-sized (one hop / one style per file), so chunks are large
# enough that a normal doc stays a single chunk; only oversized docs split.
CHUNK_SIZE = 2000
CHUNK_OVERLAP = 200


def infer_type(path: Path) -> str:
    """Infer entity type from the file location/name.

    data/styles/*.md          -> style
    data/ingredients/hop-*.md -> hop
    data/ingredients/malt-*.md -> malt
    data/ingredients/yeast-*.md -> yeast
    anything else             -> ingredient
    """
    if path.parent.name == "styles":
        return "style"
    prefix = path.stem.split("-", 1)[0]
    if prefix in ("hop", "malt", "yeast"):
        return prefix
    return "ingredient"


def extract_title(text: str, fallback: str) -> str:
    """Return the first markdown H1 heading, or a fallback."""
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def load_documents(data_dir: Path) -> list[Document]:
    """Load all .md/.txt files under data_dir into LangChain Documents."""
    docs: list[Document] = []
    for pattern in ("**/*.md", "**/*.txt"):
        for path in sorted(data_dir.glob(pattern)):
            text = path.read_text(encoding="utf-8")
            metadata = {
                "source": str(path.relative_to(data_dir)),
                "type": infer_type(path),
                "title": extract_title(text, path.stem),
            }
            docs.append(Document(page_content=text, metadata=metadata))
    return docs


def main() -> None:
    cfg = load_config()
    data_dir: Path = cfg["data_dir"]
    chroma_dir: Path = cfg["chroma_dir"]

    docs = load_documents(data_dir)
    if not docs:
        sys.exit(f"No .md/.txt documents found under {data_dir}")
    print(f"Loaded {len(docs)} documents from {data_dir}")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
    )
    chunks = splitter.split_documents(docs)
    split_count = sum(1 for d in chunks if len(d.page_content) >= CHUNK_SIZE)
    print(
        f"Chunked into {len(chunks)} chunks "
        f"(chunk_size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP}; "
        f"{split_count} docs were split)"
    )

    embeddings = HuggingFaceEmbeddings(model_name=cfg["embedding_model"])
    print(f"Embedding model: {cfg['embedding_model']} (first run downloads it)")

    client = chromadb.PersistentClient(path=str(chroma_dir))
    existing = [c.name for c in client.list_collections()]
    if COLLECTION_NAME in existing:
        client.delete_collection(COLLECTION_NAME)
        print(f"Deleted existing collection '{COLLECTION_NAME}' (re-ingest)")

    vectorstore = Chroma(
        client=client,
        collection_name=COLLECTION_NAME,
        embedding_function=embeddings,
    )
    ids = vectorstore.add_documents(chunks)
    print(f"Stored {len(ids)} chunks in '{COLLECTION_NAME}' at {chroma_dir}")

    # Quick sanity summary by entity type
    counts: dict[str, int] = {}
    for d in chunks:
        counts[d.metadata["type"]] = counts.get(d.metadata["type"], 0) + 1
    print("By type: " + ", ".join(f"{t}={n}" for t, n in sorted(counts.items())))


if __name__ == "__main__":
    main()
