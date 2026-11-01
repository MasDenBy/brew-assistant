"""Quick retrieval sanity check (dev only, not part of the app)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chromadb
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

from config import load_config

cfg = load_config()
client = chromadb.PersistentClient(path=str(cfg["chroma_dir"]))
vs = Chroma(
    client=client,
    collection_name="brew_docs",
    embedding_function=HuggingFaceEmbeddings(model_name=cfg["embedding_model"]),
)
results = vs.similarity_search_with_score("Can I substitute Cascade hops?", k=4)
for doc, score in results:
    print(f"{score:.3f}  [{doc.metadata['type']}] {doc.metadata['source']}")
