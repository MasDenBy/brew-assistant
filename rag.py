"""Retrieval + generation chain for brew-assistant.

Model name/temperature/top_k come from config.yaml only — never hardcode them.
"""
from functools import lru_cache

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_ollama import ChatOllama

from config import load_config

COLLECTION_NAME = "brew_docs"

SYSTEM_PROMPT = """\
You are Brew Assistant, a knowledgeable home brewing expert helping home \
brewers with beer styles, ingredients (hops, malts, yeasts), and substitutions.

Rules:
- Answer using ONLY the provided context documents. Each document block is \
labeled with its source filename and type.
- If the context does not contain the answer, say you don't have that \
information in your knowledge base. Do not guess.
- Never invent numbers (IBU, ABV, SRM, gravity, temperature, alpha acids). \
Only state numbers that appear in the context.
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
        ("human", "{question}"),
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
    """Build the chat model from config (or explicit overrides from the UI)."""
    cfg = load_config()
    return ChatOllama(
        model=model or cfg["model"],
        temperature=cfg["temperature"] if temperature is None else temperature,
    )


def retrieve(query: str, k: int | None = None) -> list[Document]:
    """Similarity search over the ingested docs; top_k from config if not given."""
    cfg = load_config()
    top_k = k or cfg["top_k"]
    return _get_vectorstore().similarity_search(query, k=top_k)


def _norm_source(source: str) -> str:
    """POSIX-style source paths so citations match across platforms."""
    return source.replace("\\", "/")


def get_retrieved_sources(query: str, k: int | None = None) -> list[dict]:
    """Retrieved chunks as plain dicts for the UI's 'show sources' panel."""
    return [
        {
            "source": _norm_source(d.metadata.get("source", "?")),
            "type": d.metadata.get("type", "?"),
            "title": d.metadata.get("title", "?"),
            "content": d.page_content,
        }
        for d in retrieve(query, k)
    ]


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


def _build_messages(question: str, history: list[tuple[str, str]] | None) -> list:
    docs = retrieve(question)
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
):
    """Yield answer tokens for streaming into Streamlit."""
    messages = _build_messages(question, history)
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
) -> tuple[str, list[dict]]:
    """Non-streaming convenience: full answer + retrieved sources (for tests)."""
    messages = _build_messages(question, history)
    answer = get_llm(model, temperature).invoke(messages).content
    if isinstance(answer, list):
        answer = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in answer
        )
    return answer, get_retrieved_sources(question)
