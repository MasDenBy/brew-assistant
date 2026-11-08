"""Retrieval + generation chain for brew-assistant.

Model name/temperature/top_k come from config.yaml only — never hardcode them.
"""
from collections import Counter
from functools import lru_cache

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_ollama import ChatOllama

from config import load_config

COLLECTION_NAME = "brew_docs"

# Entity types produced by ingest (see ingest.py infer_type). Order is the
# sidebar display order; unknown future types are appended alphabetically.
TYPE_ORDER = (
    "style", "bjcp_style", "hop", "malt", "yeast", "recipe", "table", "ingredient",
)
TYPE_LABELS = {
    "style": "Style docs (repo)",
    "bjcp_style": "BJCP guidelines",
    "hop": "Hops",
    "malt": "Malts",
    "yeast": "Yeasts",
    "recipe": "Recipes",
    "table": "Reference tables",
    "ingredient": "Other ingredients",
}

SYSTEM_PROMPT = """\
You are Brew Assistant, a knowledgeable home brewing expert helping home \
brewers with beer styles, ingredients (hops, malts, yeasts), and substitutions.

Rules:
- Answer using ONLY the provided context documents. Each document block is \
labeled with its source filename and type.
- Check the context carefully before answering: the answer is often there, \
including exact numbers (IBU, ABV, SRM, gravity) and strain or ingredient \
names. When it is, quote those values exactly as written in the context.
- Only say "I don't have that information" if the context genuinely lacks \
the answer — first make sure you have read the whole context.
- Never invent numbers, yeast strain names, or filenames. If it is not in \
the context, do not state it.
- When you use information from a document, cite it inline using the source \
filename, e.g. [source: ingredients/hop-cascade.md]. Copy the filename \
EXACTLY as it appears in the context blocks — never invent a filename. If \
you did not use any document, do not add a citation.
- For ingredient substitution questions, prefer substitutes explicitly \
mentioned in the context.
- Be concise and practical: your reader is a home brewer at their bench.
"""

PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_PROMPT),
        ("placeholder", "{history}"),
        # The retrieved context MUST be referenced here — a template variable
        # that is passed but never rendered is silently dropped (bug present
        # since Step 5: answers were model priors, not grounded content).
        ("human", "Context documents:\n{context}\n\nQuestion: {question}"),
    ]
)


@lru_cache(maxsize=1)
def _get_vectorstore() -> Chroma:
    """Cached so Streamlit reruns don't reload the embedding model."""
    cfg = load_config()
    embeddings = HuggingFaceEmbeddings(model_name=cfg["embedding_model"])
    import chromadb

    client = chromadb.PersistentClient(path=str(cfg["chroma_dir"]))
    return Chroma(
        client=client,
        collection_name=COLLECTION_NAME,
        embedding_function=embeddings,
    )


def get_llm(model: str | None = None, temperature: float | None = None) -> ChatOllama:
    """Build the chat model from config (or explicit overrides from the UI).

    ``num_ctx`` comes from config so the retrieved context (top_k chunks of
    up to 2600 chars) plus chat history fits the model's window.
    """
    cfg = load_config()
    return ChatOllama(
        model=model or cfg["model"],
        temperature=cfg["temperature"] if temperature is None else temperature,
        num_ctx=cfg["num_ctx"],
    )


@lru_cache(maxsize=1)
def type_counts() -> dict[str, int]:
    """Chunk count per entity type in the store (cached per process).

    ~25–35 ms for 1,285 rows; cached so Streamlit reruns do not rescan.
    Call `reset_caches()` after re-ingesting.
    """
    metadatas = _get_vectorstore().get(include=["metadatas"]).get("metadatas") or []
    return dict(Counter(m.get("type", "ingredient") for m in metadatas))


def scope_options() -> list[str]:
    """Search-scope choices: known types first (TYPE_ORDER), then the rest."""
    counts = type_counts()
    known = [t for t in TYPE_ORDER if t in counts]
    return known + sorted(t for t in counts if t not in TYPE_ORDER)


def reset_caches() -> None:
    """Drop cached vectorstore + type counts (after re-ingest)."""
    _get_vectorstore.cache_clear()
    type_counts.cache_clear()


def type_filter(types: list[str] | None) -> dict | None:
    """Chroma `where` clause for the search scope.

    Empty/None means "no filter" — search every type. That is deliberate:
    a cleared sidebar multiselect must not produce empty context.
    """
    cleaned = [t for t in (types or []) if t]
    return {"type": {"$in": cleaned}} if cleaned else None


def retrieve(query: str, k: int | None = None, types: list[str] | None = None) -> list[Document]:
    """Similarity search; k from config when omitted, optional type scope.

    A narrow scope may legitimately return fewer than k documents (e.g. the
    `table` type currently has 4 chunks).
    """
    cfg = load_config()
    return _get_vectorstore().similarity_search(
        query, k=k or cfg["top_k"], filter=type_filter(types)
    )


def _norm_source(source: str) -> str:
    """POSIX-style source paths so citations match across platforms."""
    return source.replace("\\", "/")


def docs_to_sources(docs: list[Document]) -> list[dict]:
    """Retrieved chunks as plain dicts for the UI's 'show sources' panel."""
    return [
        {
            "source": _norm_source(d.metadata.get("source", "?")),
            "type": d.metadata.get("type", "?"),
            "title": d.metadata.get("title", "?"),
            "content": d.page_content,
        }
        for d in docs
    ]


def get_retrieved_sources(
    query: str, k: int | None = None, types: list[str] | None = None
) -> list[dict]:
    """Retrieved chunks as plain dicts (kept for tests/back-compat)."""
    return docs_to_sources(retrieve(query, k, types))


def format_context(docs: list[Document]) -> str:
    """Render retrieved docs as numbered blocks for the prompt."""
    if not docs:
        return "(no relevant documents retrieved)"
    blocks = []
    for i, d in enumerate(docs, 1):
        src = _norm_source(d.metadata.get("source", "?"))
        dtype = d.metadata.get("type", "?")
        blocks.append(f"[{i}] source: {src} (type: {dtype})\n{d.page_content}")
    return "\n\n".join(blocks)


def _build_messages(
    question: str,
    history: list[tuple[str, str]] | None,
    docs: list[Document] | None = None,
) -> list:
    docs = retrieve(question) if docs is None else docs
    return PROMPT.format_messages(
        history=history or [],
        question=question,
        context=format_context(docs),
    )


def stream_answer(
    question: str,
    history: list[tuple[str, str]] | None = None,
    model: str | None = None,
    temperature: float | None = None,
    docs: list[Document] | None = None,
):
    """Yield answer tokens. Pass `docs` to reuse an already-filtered retrieval."""
    messages = _build_messages(question, history, docs)
    llm = get_llm(model, temperature)
    for chunk in llm.stream(messages):
        content = chunk.content
        if isinstance(content, list):  # models that emit content parts
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        if content:
            yield content


def ask(
    question: str,
    history: list[tuple[str, str]] | None = None,
    model: str | None = None,
    temperature: float | None = None,
    types: list[str] | None = None,
) -> tuple[str, list[dict]]:
    """Non-streaming convenience: full answer + retrieved sources (for tests)."""
    docs = retrieve(question, types=types)
    messages = _build_messages(question, history, docs)
    answer = get_llm(model, temperature).invoke(messages).content
    if isinstance(answer, list):
        answer = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in answer
        )
    return answer, docs_to_sources(docs)
