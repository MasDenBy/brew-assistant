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


_CITATION_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def _parse_citation(marker: str) -> list[int]:
    """[1, 2, 3] -> [1, 2, 3]."""
    return [int(n.strip()) for n in marker.split(",")]


def _format_citation(numbers: list[int]) -> str:
    return f"[{', '.join(str(n) for n in numbers)}]"


def normalize_citations(answer: str) -> str:
    """Post-process an answer to reduce citation spam.

    Rules applied per paragraph/list block:
    - Collapse adjacent citations: ``[2] [3]`` -> ``[2, 3]``.
    - Remove duplicate citations within the same block (second ``[2]`` drops).
    - Strip all citations from a canonical refusal phrase.
    - Preserve the order of first appearance within each block.
    - Never invent new citations.
    """
    if not answer:
        return answer

    def process_block(block: str) -> str:
        # Refusal block: strip every citation.
        first_line = block.strip().splitlines()[0] if block.strip() else ""
        cleaned_first = first_line.lower().replace("\u2019", "'").lstrip("*#>- ")
        is_refusal_block = cleaned_first.startswith(REFUSAL_PHRASE)

        # Pass 1: deduplicate and drop empty markers.
        seen: set[int] = set()
        fragments = []
        last_end = 0
        for match in _CITATION_RE.finditer(block):
            numbers = _parse_citation(match.group(1))
            before = block[last_end:match.start()]
            if is_refusal_block:
                fragments.append(before)
                last_end = match.end()
                continue
            kept = [n for n in numbers if n not in seen]
            seen.update(kept)
            if kept:
                fragments.append(before)
                fragments.append(_format_citation(kept))
            else:
                # Dropped duplicate/refusal marker: swallow one preceding space.
                fragments.append(before.rstrip(" "))
            last_end = match.end()
        fragments.append(block[last_end:])
        text = "".join(fragments)

        # Pass 2: collapse adjacent markers separated only by whitespace.
        collapsed = []
        last_end = 0
        prev_nums: list[int] | None = None
        prev_start = -1
        for match in _CITATION_RE.finditer(text):
            between = text[last_end:match.start()]
            nums = _parse_citation(match.group(1))
            if prev_nums is not None and between.strip() == "":
                # Merge with the previous marker.
                merged_set = set(prev_nums)
                merged = list(prev_nums)
                for n in nums:
                    if n not in merged_set:
                        merged.append(n)
                        merged_set.add(n)
                collapsed[-1] = _format_citation(merged)
                prev_nums = merged
            else:
                collapsed.append(text[last_end:match.start()])
                collapsed.append(_format_citation(nums))
                prev_nums = nums
            last_end = match.end()
        collapsed.append(text[last_end:])
        return "".join(collapsed)

    # Process paragraph blocks separately so citations across paragraphs are
    # preserved, but duplicates within a paragraph/list are collapsed.
    blocks = answer.split("\n\n")
    return "\n\n".join(process_block(b) for b in blocks)


SYSTEM_PROMPT = """\
You are Brew Assistant, a knowledgeable home brewing expert helping home \
brewers with beer styles, ingredients (hops, malts, yeasts), and substitutions.

You have two sources: the numbered context documents below and your own \
brewing knowledge. Decide per question which mode applies.

FACTUAL questions — specific numbers, product specs, style stats, \
substitution tables, recipe contents ("what is", "how much", "which values"):
- Answer using ONLY the provided context documents; each document block is \
labeled with its source filename and type. Check the context carefully \
before answering: the answer is often there, including exact numbers (IBU, \
ABV, SRM, gravity) and strain or ingredient names. When it is, quote those \
values exactly as written in the context.
- If the context genuinely lacks the answer — after reading the whole \
context carefully — reply with exactly "I don't have that information." as \
the very first sentence. You may add one short sentence naming what kind of \
document would help, and nothing else.
- Never attach citations to a refusal: a reply starting with "I don't have \
that information." must contain no [N] citations, numbers, or speculation \
about the answer.

ADVICE questions — opinions, comparisons, recommendations, "which X should \
I use", "how do I", "why", troubleshooting, process guidance:
- Combine your own brewing knowledge with the context documents and give a \
real recommendation. The documents ground the answer (styles, ingredients, \
specs); your knowledge adds the reasoning, trade-offs, and practical options \
the documents lack. Never answer an advice question with "I don't have that \
information" when you know the answer from brewing knowledge.
- Where the documents are silent, speak as general brewing advice — do not \
present it as a fact quoted from a document, and do not cite a document for it.
- Do not contradict the context about an ingredient, strain, or style it \
describes. If your general advice and a document disagree, follow the \
document and say so.
- Typical ranges from experience are fine when labeled as typical \
("usually", "a good range is"); never present them as exact document values.
- When it matters for trust, make the split visible: document facts first \
(cited), then your advice ("My take: ...").

NUMBER SAFETY — applies in both modes:
- Never sum, average, scale, or extrapolate quantities across entries. A \
number that is not stated verbatim in the context is not in the context; say \
so instead of computing one. Do not calculate recipe numbers (IBU, ABV, SRM, \
mash adjustments); if asked for one, name the data that would be needed.
- Exception: when the user asks how MUCH of something they need and the \
context states a dosage rate (e.g. "50 to 80 g/hl"), multiply that one \
stated rate by the user's own batch volume, show the arithmetic (19 L = \
0.19 hl; 50-80 g/hl x 0.19 hl = 9.5-15.2 g), and round to a practical \
amount. Use only a rate stated verbatim in the context — never derive a \
new rate.
- Never invent numbers, yeast strain names, or filenames. If it is not in \
the context, do not state it as a document fact.
- If a question is not about beer or brewing at all, reply with exactly \
"I don't have that information." as the very first sentence, with no \
citations and no speculation.

CITATIONS — apply in both modes:
- When you use information from a document, cite it inline as [N], where N is \
the number of that document's context block (the context shows every block \
numbered as "[N] source: ..."). Cite the document you actually read (for \
example the substitution table or a hop doc) — never a document that is not \
shown in the context; an ingredient merely appearing in your answer is not a \
citation. Never invent a number and never cite a block number from these \
instructions. If you did not use any document, do not add a citation. \
Only that exact bracketed-number form ([2]) is a valid citation; write [2], \
not "document 2" or "[block 2]".\n- CITE ONCE PER PARAGRAPH. Place the citation at the end of the paragraph or \
list that presents the facts, not after every sentence. Example of good \
density: "American IPA is a hoppy, moderately strong ale [1]. It showcases \
citrus, floral, or resinous American hop varieties [1]." — both sentences \
share one [1] at the end of the paragraph.\n- DO NOT do this: "American IPA is hoppy [1]. It is moderately strong [1]. It \
showcases American hops [1]." That is citation spam and is not allowed.
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
        ("human", "Context documents:\n{context}\n\nQuestion: {question}\n\nRemember: cite each document at most once per paragraph/list."),
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
    _store_dump.cache_clear()
    _part_index.cache_clear()


def type_filter(types: list[str] | None) -> dict | None:
    """Chroma `where` clause for the search scope.

    Empty/None means "no filter" — search every type. That is deliberate:
    a cleared sidebar multiselect must not produce empty context.
    """
    cleaned = [t for t in (types or []) if t]
    return {"type": {"$in": cleaned}} if cleaned else None


# ---------------------------------------------------------------------------
# Exact part-number retrieval
#
# Embeddings carry no signal for opaque product codes ("OYL-042"): all
# sibling strains of a lab embed nearly identically, so the specific strain
# asked for can fall out of top-k entirely (verified: "Tell me about a
# OYL-042 yeasts" retrieved 8 arbitrary Omega strains, not OYL-042). Product
# numbers are therefore matched EXACTLY against the stored documents in
# code, and the matching chunks are pinned at the front of the context.
# ---------------------------------------------------------------------------

# Product codes: OYL-042 / WLP300 / PIN-001Dry / us-05 / S-04 (1+ letters +
# optional '-' + 2+ digits; no space, or "Wyeast 3068" would match "Wyeast
# 30"), plus a bare 4-digit Wyeast-style number (3068, 2035-PC). Single-letter
# prefixes (S-04, T-58, K-97, E-30, W-34/70) are Fermentis product codes and
# MUST pin like the rest. A wrong match requires an exact normalized-token
# collision with document content, which plain question text does not produce.
_PART_TOKEN_RE = re.compile(
    r"\b[A-Za-z]{1,6}-?\d{2,6}[A-Za-z0-9]*\b|\b\d{4}(?:-PC)?\b",
    re.IGNORECASE,
)
MAX_PINNED_DOCS = 4


def _norm_part(token: str) -> str:
    """``OYL-042`` / ``oyl 042`` / ``OYL042`` -> ``OYL042``."""
    return re.sub(r"[^A-Za-z0-9]+", "", token).upper()


@lru_cache(maxsize=1)
def _store_dump() -> tuple[tuple[str, ...], tuple[dict, ...]]:
    """All (document, metadata) pairs, cached per process (see type_counts)."""
    data = _get_vectorstore().get(include=["documents", "metadatas"])
    documents = tuple(data.get("documents") or [])
    metadatas = tuple(data.get("metadatas") or [])
    return documents, metadatas


@lru_cache(maxsize=1)
def _part_index() -> dict[str, tuple[tuple[int, int], ...]]:
    """Normalized part token -> (priority, index) pairs into the dump.

    Priority ranks HOW the token matched: 0 = the source filename contains
    it (``yeast-3068-weihenstephan-weizen.md``), 1 = the chunk title, 2 =
    anywhere in the text (a fixture doc that merely mentions "Wyeast 3068").
    Lower priority wins; ties break on store order (earlier chunk first).
    """
    documents, metadatas = _store_dump()
    best: dict[str, dict[int, int]] = {}
    for i, (text, meta) in enumerate(zip(documents, metadatas)):
        meta = meta or {}
        stem = _norm_part(str(meta.get("source", "")).rsplit("/", 1)[-1])
        title = _norm_part(str(meta.get("title", "")))
        for token in _PART_TOKEN_RE.findall(text):
            norm = _norm_part(token)
            priority = 0 if norm in stem else 1 if norm in title else 2
            slot = best.setdefault(norm, {})
            if slot.get(i, 3) > priority:
                slot[i] = priority
    return {
        key: tuple(sorted((p, i) for i, p in slots.items()))
        for key, slots in best.items()
    }


def part_number_docs(query: str) -> list[Document]:
    """Stored documents that exactly match a part number in ``query``.

    Deterministic (no embedding): each product-code token in the question is
    normalized and looked up in the store; filename matches rank above title
    matches above plain mentions, one chunk per source, at most
    :data:`MAX_PINNED_DOCS`.
    """
    tokens = {_norm_part(token) for token in _PART_TOKEN_RE.findall(query)}
    if not tokens:
        return []
    documents, metadatas = _store_dump()
    index = _part_index()
    candidates = []
    for token in sorted(tokens):
        candidates.extend(index.get(token, ()))
    candidates.sort()  # (priority, index): best match kind, earliest chunk

    pinned: list[Document] = []
    seen_sources: set[str] = set()
    for _, i in candidates:
        meta = metadatas[i] or {}
        source = _norm_source(str(meta.get("source", "?")))
        if source in seen_sources:
            continue
        seen_sources.add(source)
        pinned.append(Document(page_content=documents[i], metadata=meta))
        if len(pinned) >= MAX_PINNED_DOCS:
            break
    return pinned


def merge_pinned(
    docs: list[Document], pinned: list[Document], limit: int
) -> list[Document]:
    """Exact part-number matches first, then similarity hits (deduped by
    source), trimmed to ``limit``."""
    if not pinned:
        return docs
    pinned_sources = {
        _norm_source(d.metadata.get("source", "?")) for d in pinned
    }
    merged = pinned + [
        d for d in docs
        if _norm_source(d.metadata.get("source", "?")) not in pinned_sources
    ]
    return merged[:limit]


def expand_query(query: str, history: list[tuple[str, str]] | None) -> str:
    """Prepend recent conversation turns so ambiguous follow-ups retrieve better.

    Short or pronoun-heavy follow-ups ("The BJCP one.", "How much Citra does
    it use?") lack the entity names the retriever needs. Re-injecting the
    most recent user question and the model's last answer keeps retrieval
    grounded in the conversation without changing the chunking or vector
    store.
    """
    if not history:
        return query
    # Prepend the previous user question; this gives short/pronoun follow-ups
    # the entity names the retriever needs without pulling in the assistant's
    # answer (which can bias retrieval toward its own keywords).
    last_user = next(
        (content for role, content in reversed(history) if role == "user"),
        "",
    )
    if not last_user:
        return query
    # Avoid duplication if the user simply repeated the same question.
    if last_user.strip().lower() == query.strip().lower():
        return query
    # Very short or selector-style follow-ups ("The BJCP one.") are better
    # retrieved by the original question, which already names the entity.
    # Longer follow-ups need both the entity name and the new detail.
    stripped_query = query.strip()
    if len(stripped_query) < 30:
        return last_user
    return f"{last_user} {stripped_query}"


def retrieve(query: str, k: int | None = None, types: list[str] | None = None) -> list[Document]:
    """Similarity search; k from config when omitted, optional type scope.

    A narrow scope may legitimately return fewer than k documents (e.g. the
    `table` type currently has 4 chunks). Part-number tokens in the query
    (``OYL-042``, ``WLP300``, ``3068``) additionally pin their exact matches
    at the front of the result — embeddings cannot rank an opaque product
    code against the sibling strains of the same lab.
    """
    cfg = load_config()
    limit = k or cfg["top_k"]
    docs = _get_vectorstore().similarity_search(
        query, k=limit, filter=type_filter(types)
    )
    pinned = part_number_docs(query)
    if pinned and types:
        allowed = set(types)
        pinned = [d for d in pinned if d.metadata.get("type") in allowed]
    return merge_pinned(docs, pinned, limit)


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


def _estimate_tokens(text: str) -> int:
    """Simple character-based token heuristic: ~4 chars per token."""
    return len(text) // 4


def trim_history(
    history: list[tuple[str, str]],
    context_text: str,
    question: str,
    num_ctx: int,
    reserved_response_tokens: int = 512,
    max_history_turns: int | None = None,
) -> list[tuple[str, str]]:
    """Return the largest suffix of history that fits in the context window.

    Budget reservation: system prompt, retrieved context, new question, and
    response headroom are subtracted first; everything left is for history.
    History is walked newest-to-oldest and the qualifying suffix is returned
    in original order. ``max_history_turns`` caps turns even when the budget
    would allow more (None/0 disables the cap).
    """
    history = history or []
    if max_history_turns is not None and max_history_turns > 0:
        history = history[-max_history_turns:]
    if not history:
        return []

    fixed_tokens = (
        _estimate_tokens(SYSTEM_PROMPT)
        + _estimate_tokens(context_text)
        + _estimate_tokens(question)
        + reserved_response_tokens
    )
    budget = max(0, num_ctx - fixed_tokens)

    # Walk newest-to-oldest, accumulate token count, then reverse the suffix.
    kept: list[tuple[str, str]] = []
    used = 0
    for role, content in reversed(history):
        cost = _estimate_tokens(content)
        if used + cost > budget and kept:
            # Stop once adding another turn would exceed the budget. The
            # newest turn is always kept, even if it alone exceeds budget,
            # so we never return an empty list when history is non-empty.
            break
        kept.append((role, content))
        used += cost
    return list(reversed(kept))


def _history_settings(cfg: dict, model: str | None) -> tuple[int, int, int | None]:
    """Resolve (num_ctx, reserved_response_tokens, max_history_turns)."""
    settings = model_settings(cfg, model or cfg["model"])
    num_ctx = settings["num_ctx"]
    response_ratio = cfg.get("response_token_ratio", 0) or 0
    reserved_response_tokens = max(512, int(num_ctx * response_ratio))
    max_history_turns = cfg.get("max_history_turns") or None
    return num_ctx, reserved_response_tokens, max_history_turns


def history_turn_count(
    history: list[tuple[str, str]],
    question: str,
    model: str | None = None,
    docs: list[Document] | None = None,
) -> int:
    """Number of history turns that would be sent for this question."""
    cfg = load_config()
    num_ctx, reserved, max_turns = _history_settings(cfg, model)
    context_text = format_context(docs) if docs is not None else ""
    trimmed = trim_history(
        history, context_text, question, num_ctx,
        reserved_response_tokens=reserved, max_history_turns=max_turns,
    )
    return len(trimmed)


def _build_messages(
    question: str,
    history: list[tuple[str, str]] | None,
    docs: list[Document] | None = None,
    model: str | None = None,
) -> list:
    if docs is None:
        docs = retrieve(question)
    cfg = load_config()
    num_ctx, reserved, max_turns = _history_settings(cfg, model)
    context_text = format_context(docs)
    trimmed_history = trim_history(
        history or [],
        context_text,
        question,
        num_ctx,
        reserved_response_tokens=reserved,
        max_history_turns=max_turns,
    )
    return PROMPT.format_messages(
        history=trimmed_history,
        question=question,
        context=context_text,
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
    """Yield the final answer (normalized citations) as one string.

    The full answer is buffered so citations can be post-processed before
    display. Pass `docs` to reuse an already-filtered retrieval.
    """
    docs = retrieve(expand_query(question, history)) if docs is None else docs
    # Deterministic multi-guideline style disambiguation bypasses the LLM.
    reply = disambiguation_reply(question, docs)
    if reply is not None:
        yield normalize_citations(reply)
        return
    messages = _build_messages(question, history, docs, model=model)
    llm = get_llm(model, temperature)
    pieces = []
    for chunk in llm.stream(messages):
        content = chunk.content
        if isinstance(content, list):  # models that emit content parts
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        if content:
            pieces.append(content)
    yield normalize_citations("".join(pieces))


def ask(
    question: str,
    history: list[tuple[str, str]] | None = None,
    model: str | None = None,
    temperature: float | None = None,
    types: list[str] | None = None,
) -> tuple[str, list[dict]]:
    """Non-streaming convenience: full answer + retrieved sources (for tests)."""
    retrieval_query = expand_query(question, history)
    docs = retrieve(retrieval_query, types=types)
    # Deterministic multi-guideline style disambiguation bypasses the LLM.
    reply = disambiguation_reply(question, docs)
    if reply is not None:
        return reply, docs_to_sources(docs)
    messages = _build_messages(question, history, docs, model=model)
    answer = get_llm(model, temperature).invoke(messages).content
    if isinstance(answer, list):
        answer = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in answer
        )
    return normalize_citations(answer), docs_to_sources(docs)
