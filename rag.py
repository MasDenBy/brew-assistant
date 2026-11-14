"""Retrieval + generation chain for brew-assistant.

Model name/temperature/top_k come from config.yaml only — never hardcode them.
"""
from collections import Counter
from functools import lru_cache
import re

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
    "style": "Style guidelines (BA + repo)",
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
Cite each document at most once per paragraph: place the [N] at the end of \
the paragraph or list that presents that document's facts, not after every \
sentence. Example: "... is 4.2-5.2% ABV [2]." Only that exact \
bracketed-number form ([2]) is a valid citation; write [2], not "document 2" \
or "[block 2]".
- If the context contains style documents from MORE THAN ONE style \
guideline (e.g., BJCP and BA) matching the question, do NOT merge them \
into one answer and never combine or average their stats (ABV, IBU, SRM, \
gravity): each guideline's style is a separate style. If the conversation \
already listed the matching styles and the user has now picked one, \
answer ONLY about that style and cite only its document.
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


# Guideline origin derived from the collector namespace (first path segment
# of the source, relative to the data dir). Explicit in the context header
# and the sources panel so the model can disambiguate BJCP vs BA styles
# without guessing from file paths. Path-derived for now; explicit metadata
# in the collectors is the future option if this proves fragile.
GUIDELINE_BY_DIR = {"bjcp": "BJCP", "ba": "BA"}


def guideline_of(source: str) -> str | None:
    """Style-guide origin for a source path ('BJCP'/'BA'), or None."""
    first = _norm_source(source).split("/", 1)[0].lower()
    return GUIDELINE_BY_DIR.get(first)


def docs_to_sources(docs: list[Document]) -> list[dict]:
    """Retrieved chunks as plain dicts for the UI's 'show sources' panel."""
    return [
        {
            "source": _norm_source(d.metadata.get("source", "?")),
            "type": d.metadata.get("type", "?"),
            "title": d.metadata.get("title", "?"),
            "guideline": guideline_of(d.metadata.get("source", "")),
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
        guideline = guideline_of(src)
        label = f", guideline: {guideline}" if guideline else ""
        blocks.append(f"[{i}] source: {src} (type: {dtype}{label})\n{d.page_content}")
    return "\n\n".join(blocks)


def _build_messages(
    question: str,
    history: list[tuple[str, str]] | None,
    docs: list[Document] | None = None,
) -> list:
    if docs is None:
        docs = retrieve(question)
    return PROMPT.format_messages(
        history=history or [],
        question=question,
        context=format_context(docs),
    )


# ---------------------------------------------------------------------------
# Deterministic style disambiguation
#
# Prompt-only disambiguation was unstable across models (placeholder echo,
# copied example wording, dropped entries, merged stats). So the
# multi-guideline disambiguation list is generated in code, without the LLM:
# when the question's content tokens are all covered by the titles of TWO OR
# MORE style documents (e.g., BJCP and BA), the reply is the fixed list of
# those styles plus a clarifying question. The LLM only answers after the
# user picks a style.
# ---------------------------------------------------------------------------

STYLE_TYPES = ("style", "bjcp_style")
DISAMBIGUATION_QUESTION = "Which style are you interested in?"

# Generic question words that never identify a style. "style" itself is
# included even though BA titles contain it ("American-Style") — hyphen
# splitting still lets title tokens match question tokens, it just stops the
# question's generic "style" from diluting the match.
_DISAMBIG_STOPWORDS = frozenset(
    """
    a about ale an and are as at be beer best brew brewer brewing but by can
    compare come describes difference do does for from get give good have
    history how i in into is it its like make me much my need of on one or
    please recommend should so style styles than that the their them there
    these they this to typical typical use used using want was well what
    when which who why will with would you your tell
    """.split()
)


def _tokens(text: str) -> frozenset:
    """Content tokens of a question or title: lowercase words, stopwords
    removed, naive singulars so 'lagers' matches title 'lager'."""
    out = set()
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        if len(tok) < 2 or tok in _DISAMBIG_STOPWORDS:
            continue
        out.add(tok[:-1] if len(tok) > 3 and tok.endswith("s") else tok)
    return frozenset(out)


def style_disambiguation_matches(
    question: str, docs: list[Document]
) -> list[tuple[int, Document]]:
    """[(retrieval index, doc)] for style docs whose title covers EVERY
    content token of the question, in retrieval order.

    Index is 1-based retrieval order so [N] citations in the reply match
    format_context() and the UI sources panel (which both show the FULL
    retrieved list, not just the style docs).
    """
    q_tokens = _tokens(question)
    if not q_tokens:
        return []
    matches = []
    for i, d in enumerate(docs, 1):
        if d.metadata.get("type") not in STYLE_TYPES:
            continue
        title_tokens = _tokens(d.metadata.get("title", ""))
        if title_tokens and q_tokens <= title_tokens:
            matches.append((i, d))
    return matches


def _first_sentences(text: str, max_chars: int = 220, max_sentences: int = 2) -> str:
    """First 1-2 sentences, capped at ~max_chars (never mid-word)."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    out = ""
    for count, sentence in enumerate(sentences, 1):
        out = f"{out} {sentence}".strip()
        if count >= max_sentences or len(out) >= max_chars:
            break
    if len(out) > max_chars:
        out = out[:max_chars].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return out


def _style_description(content: str) -> str:
    """Pure prose impression of a style doc (no stats, no meta lines).

    Prefers the BJCP 'Overall impression' section; otherwise the first prose
    lines of the document, skipping headings, 'Key: value' meta lines, and
    vital-statistics lines.
    """
    impression: list[str] = []
    prose: list[str] = []
    in_impression = False
    for line in content.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            heading = s.lstrip("#").strip().lower()
            in_impression = "impression" in heading or "introduction" in heading
            continue
        if re.match(r"^[A-Z][\w &/']*: ", s):  # Category:/Family:/Source: meta
            continue
        if "OG:" in s or "IBU:" in s or re.match(r"^(OG|FG|ABV|IBU|Color)\b", s):
            continue  # vital-statistics lines
        if in_impression:
            impression.append(s)
        elif not prose:
            prose.append(s)
    if impression:
        return _first_sentences(" ".join(impression))
    return _first_sentences(" ".join(prose))


def _style_name_line(title: str, guideline: str | None, index: int) -> str:
    """'1A American Light Lager (BJCP style) [4]'.

    [N] is the doc's retrieval index so the citation matches the sources
    panel. The guideline suffix is added unless the title already carries it.
    """
    suffix = f" ({guideline} style)"
    if not guideline or suffix.lower() in title.lower():
        return f"{title} [{index}]"
    return f"{title}{suffix} [{index}]"


def disambiguation_reply(question: str, docs: list[Document]) -> str | None:
    """The fixed multi-style disambiguation reply, or None when it does not
    apply (fewer than two matching style docs)."""
    matches = style_disambiguation_matches(question, docs)
    if len(matches) < 2:
        return None
    entries = []
    for i, d in matches:
        src = _norm_source(d.metadata.get("source", "?"))
        title = d.metadata.get("title") or src
        name_line = _style_name_line(title, guideline_of(src), i)
        entries.append(f"{name_line}\n{_style_description(d.page_content)}")
    return "\n\n".join(entries) + f"\n\n{DISAMBIGUATION_QUESTION}"


def stream_answer(
    question: str,
    history: list[tuple[str, str]] | None = None,
    model: str | None = None,
    temperature: float | None = None,
    docs: list[Document] | None = None,
):
    """Yield answer tokens. Pass `docs` to reuse an already-filtered retrieval."""
    docs = retrieve(question) if docs is None else docs
    # Deterministic multi-guideline style disambiguation bypasses the LLM.
    reply = disambiguation_reply(question, docs)
    if reply is not None:
        yield reply
        return
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
    # Deterministic multi-guideline style disambiguation bypasses the LLM.
    reply = disambiguation_reply(question, docs)
    if reply is not None:
        return reply, docs_to_sources(docs)
    messages = _build_messages(question, history, docs)
    answer = get_llm(model, temperature).invoke(messages).content
    if isinstance(answer, list):
        answer = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in answer
        )
    return answer, docs_to_sources(docs)
