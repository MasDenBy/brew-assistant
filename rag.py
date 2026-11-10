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

from config import load_config, model_settings

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

# Canonical no-answer phrase. The system prompt makes the model start
# refusals with it verbatim; app.py uses is_refusal() to detect it and hide
# the (irrelevant) retrieved sources for such answers.
REFUSAL_PHRASE = "i don't have that information"


def is_refusal(text: str) -> bool:
    """True when an answer is the canonical no-answer refusal.

    Only the first line is inspected: trailing elaboration ("... about wine
    yeasts") still counts, but an answer that merely mentions the phrase
    mid-answer does not. Markdown list/quote/bold prefixes and typographic
    apostrophes are normalized before matching.
    """
    stripped = (text or "").strip()
    if not stripped:
        return False
    first_line = stripped.splitlines()[0]
    first_line = first_line.lower().replace("\u2019", "'")
    return first_line.lstrip("*#>- ").startswith(REFUSAL_PHRASE)


SYSTEM_PROMPT = """\
You are Brew Assistant, a knowledgeable home brewing expert helping home \
brewers with beer styles, ingredients (hops, malts, yeasts), and substitutions.

Rules:
- Answer using ONLY the provided context documents. Each document block is \
labeled with its source filename and type.
- Check the context carefully before answering: the answer is often there, \
including exact numbers (IBU, ABV, SRM, gravity) and strain or ingredient \
names. When it is, quote those values exactly as written in the context.
- Never sum, average, scale, or extrapolate quantities across entries. A \
number that is not stated verbatim in the context is not in the context; say \
so instead of computing one.
- If the context genuinely lacks the answer — after reading the whole context \
carefully — reply with exactly "I don't have that information." as the very \
first sentence. You may add one short sentence naming what kind of document \
would help, and nothing else.
- Never attach citations to a refusal: a reply starting with "I don't have \
that information." must contain no [N] citations, numbers, or speculation \
about the answer.
- Never invent numbers, yeast strain names, or filenames. If it is not in \
the context, do not state it.
- When you use information from a document, cite it inline as [N], where N is \
the number of that document's context block (the context shows every block \
numbered as "[N] source: ..."). Cite the document you actually read (for \
example the substitution table or a hop doc) — never a document that is not \
shown in the context; an ingredient merely appearing in your answer is not a \
citation. Never invent a number and never cite a block number from these \
instructions. If you did not use any document, do not add a citation. \
Example: "... is 4.2-5.2% ABV [2]." Only that exact bracketed-number form \
([2]) is a valid citation; write [2], not "document 2" or "[block 2]".
- For ingredient substitution questions, prefer substitutes explicitly \
mentioned in the context. Careful with direction: a table row "X: Y, Z" \
means Y and Z are used INSTEAD OF X. If asked what to use instead of \
Cascade hops (or any other ingredient), name only substitutes listed FOR \
that ingredient — do not name an ingredient merely because it appears \
somewhere inside a row, and do not reverse a row. If a substitute is only \
named inside another document (a table row or a hop description), cite that \
document — never a separate file for the substitute that is not shown in \
the context. Put the citation on the sentence that introduces the list; do \
not add one [N] citation per list item.
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

    Precedence for `temperature`: explicit argument (UI slider) -> per-model
    override in config.yaml `models:` -> global `temperature`. `num_ctx` is
    config-only (per-model override, else global) — a VRAM decision, not a
    per-question one, so the UI does not expose it.
    """
    cfg = load_config()
    settings = model_settings(cfg, model or cfg["model"])
    return ChatOllama(
        model=model or cfg["model"],
        temperature=settings["temperature"] if temperature is None else temperature,
        num_ctx=settings["num_ctx"],
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
