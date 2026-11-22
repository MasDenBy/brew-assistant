# AGENTS.md — Home Brewing Assistant

**Purpose:** This file gives any agent working in this repository a shared understanding of the project, stack, and conventions. Always read this file first, then read `BREW_BOT_PLAN.md` for the full plan before making changes.

---

## 1. Project Goal (One Sentence)

Build a **local chat application** that helps home brewers with brewing ingredient knowledge, ingredient substitutions, and beer recipe creation, using **open-source LLMs with RAG** (Retrieval-Augmented Generation).

---

## 2. Primary Source of Truth

**`BREW_BOT_PLAN.md`** — the complete project plan (architecture, stack, roadmap, data sources, model recommendations, open questions).  
This `AGENTS.md` is a quick-reference summary only. If there is a conflict, `BREW_BOT_PLAN.md` wins.

---

## 3. Key Facts at a Glance

| Item | Value |
|------|-------|
| **Working directory** | `C:\Ihar\Projects\brew-assistant` |
| **Deployment** | Local machine only |
| **Hardware** | 32 GB RAM, NVIDIA RTX 3060 (12 GB VRAM), CUDA supported |
| **Tech stack** | Python |
| **Frontend** | Streamlit (recommended; Gradio is the alternative under assessment) |
| **Backend / RAG** | LangChain or LlamaIndex (under assessment) |
| **Model serving** | Ollama (local) |
| **Embeddings** | sentence-transformers (`all-MiniLM-L6-v2` or `BAAI/bge-small-en`) |
| **Vector store** | ChromaDB (local, SQLite + DuckDB) |
| **Data formats** | BeerXML (recipes), plain text / Markdown / CSV (styles, ingredients) |
| **Model class** | 7B class open-source LLM (quantized, fits RTX 3060) |
| **Model swapping** | Must be a config change, not a code change |
| **Privacy** | All data and models stay on the local machine |

---

## 4. Architecture (Summary)

```
User
  │
▼
Streamlit (chat UI)
  │
▼
Python backend (LangChain / LlamaIndex)
  │
├─► Retrieval: ChromaDB ← Embedding model (sentence-transformers)
│       ▲
│       │ Ingestion: BeerXML + text docs → chunks → embeddings → ChromaDB
│       │
├─► Generation: Local LLM via Ollama API (7B quantized, swappable)
│
▼
Answer to user (with source citations)
```

---

## 5. MVP Scope

- Chat UI for brewing questions
- Knowledge about beer styles (BJCP-style guidelines)
- Ingredient substitution help (e.g., "I don't have Cascade hops, what can I use?")
- Recipe creation assistance
- Source citations in answers
- Model selector in UI sidebar
- Re-ingest data button

**Out of scope for MVP:** Fine-tuning, recipe calculator (IBU/ABV/SRM), batch scaling, off-flavor troubleshooting, multi-user/auth.

---

## 6. Conventions for Agents

1. **Read `BREW_BOT_PLAN.md` before writing code.** It contains the full plan, roadmap, and open questions.
2. **Keep the model abstraction.** Do not hardcode a specific Ollama model name in business logic. Use a config/`.env` value so models can be swapped by configuration only.
3. **Python only.** No JavaScript/TypeScript for the frontend unless the stack decision changes.
4. **Local-first.** Do not introduce cloud APIs, external model endpoints, or telemetry that sends data off the machine.
5. **RAG-first.** Do not attempt to cram brewing knowledge into system prompts. Use ChromaDB + sentence-transformers for retrieval.
6. **Chunk by entity type.** One hop per chunk, one recipe per chunk, one style guideline per chunk. Store metadata (`source`, `type`: hop/malt/yeast/recipe/style).
7. **Cite sources.** Answers should reference which document(s) the information came from.
8. **No fine-tuning in MVP.** Tuning means retrieval quality (chunk size, top-K, embedding model) and system prompt adjustments, not model training.

### 6.1 Style disambiguation & citations (decided 2026-11-11)

When the user asks about a beer style that exists in more than one guideline
(e.g., "American Light Lager" in both BJCP and BA):

- The model must NOT merge styles from different guidelines into one answer,
  and must NOT combine their stats (ABV/IBU/SRM/gravity), even if similar.
  Styles from different guidelines are separate entries.
- Instead it replies with a short disambiguation list — one entry per matching
  style, each with the guideline name and a pure 1–2 sentence description
  (no key-stats block), cited once on the name line, ending with a clarifying
  question ("Which style are you interested in?").
- After the user picks a style, the answer covers only that one document.

Citation density:
- One [N] citation per paragraph/section (at its end), not after every sentence.
- The substitution-list rule (one citation on the intro sentence, none per
  list item) stays unchanged.

Implementation notes:
- Deterministic disambiguation (rag.py, "Deterministic style disambiguation"):
  the multi-guideline list is generated in CODE, not by the LLM. Prompt-only
  disambiguation proved unstable across models (placeholder echo, copied
  example wording, dropped entries, merged stats over 5 prompt variants).
  `style_disambiguation_matches()` matches a question against style docs
  (types `style`/`bjcp_style`) whose TITLE covers every content token of the
  question (stopwords removed, naive singulars); with 2+ matches,
  `disambiguation_reply()` emits the fixed list (title line + guideline +
  one [N] retrieval-order citation, then 1-2 pure prose sentences from the
  doc — BJCP "Overall impression" section preferred) ending with
  "Which style are you interested in?". `ask()`/`stream_answer()` return it
  directly; the LLM answers only after the user picks a style.
- Guideline origin is path-derived in `rag.py` (`guideline_of`: `bjcp/` →
  BJCP, `ba/` → BA) and shown in the context block header
  `(type: ..., guideline: BA)` plus the sources panel, so the model can
  disambiguate without guessing from file paths. No re-ingestion required;
  explicit `guideline` metadata in the collectors + re-ingest is the future
  option if path-derivation proves fragile.
- BA styles are labeled "Style guidelines (BA + repo)" in the sidebar scope
  (previously the generic "Style docs (repo)").
- Known follow-up: disambiguation relies on retrieval fetching BOTH guideline
  docs in top-K. If one routinely drops out, add per-guideline coverage for
  style queries (out of scope for now).
- Unit tests: tests/test_style_disambiguation.py (fake docs, no LLM).
- Eval: "Tell me about American Light Lager." in `tests/eval_questions.csv`
  asserts both styles listed separately, one citation each, no merged stats,
  ends with a clarifying question.

---

### 6.2 Two-tier answer policy (decided 2026-11-22)

The assistant is a colleague, not a search box. The old prompt-only rule
"answer ONLY from context" made it refuse opinion/synthesis questions
("which hops for a hoppy saison?") and hide the *why* behind grounded
answers (S-04 "starter" → bare rehydration steps, with no explanation that
dry yeast needs no starter).

- `SYSTEM_PROMPT` (rag.py) defines two modes:
  - FACTUAL (numbers, product specs, style stats, substitution tables,
    recipe contents): RAG-only, verbatim quoting, canonical refusal
    ("I don't have that information.") when the docs lack the answer.
  - ADVICE (opinions, comparisons, "which X should I use", troubleshooting,
    "how do I"): model knowledge + context grounding, real recommendation
    required; general knowledge carries no citation and is never presented
    as a document fact; document/general-knowledge contradictions resolve
    in favor of the documents.
- NUMBER SAFETY is global (both modes): no summing/averaging/scaling or
  computing IBU/ABV/SRM; no invented numbers as document facts; off-topic
  (non-brewing) questions still refuse with the canonical phrase, and the
  UI still hides sources for refusals (`is_refusal` unchanged).
- Deliberately rejected — local-first stays a HARD constraint
  (reconfirmed 2026-11-22): web search, and an OpenAI sub-agent escalation
  for "unsure" cases. Local Q/A logging for a future eval/fine-tuning
  dataset: proposed, deferred (separate task).
- Eval: two regression rows appended to tests/eval_questions.csv (hoppy
  saison advice, S-04 starter) — need a live eval re-run. Refusal rows
  must keep refusing: Maris Otter substitution (row 10), chocolate malt
  (12), IBU calculation (13), Dead Pony Club mash pH (24).
- tests/test_refusal.py unchanged (phrase + detector untouched).

Follow-up fixes, same day (2026-11-22) — "how many packs of S-04 for 19 L
at OG 20 °P" still refused with the two-tier prompt in place. Three stacked
causes, all fixed:

1. Retrieval pin gap: `_PART_TOKEN_RE` required 2+ letters before the code,
   so Fermentis single-letter codes (S-04, T-58, K-97, E-30, W-34/70) never
   pinned — even with "S-04" in the query the doc fell out of top-8
   (verified: top-8 was all White Labs/Wyeast/recipes). Regex now allows
   1+ letters; tests/test_part_number_retrieval.py covers S-04/T-58/K-97.
2. Collector gap: Fermentis pages state pack sizes ("Available in 11,5g –
   100g – 500g and 10kg packaging.") but the collector dropped the line.
   New `packaging` field → rendered as a "Packaging:" Key Specs bullet.
   Requires re-collect (python -m collectors) + re-ingest.
3. Prompt gap: NUMBER SAFETY forbade the dosage arithmetic. New exception:
   one rate stated verbatim in the context × the user's own batch volume,
   with the arithmetic shown (19 L = 0.19 hl; 50-80 g/hl × 0.19 hl =
   9.5-15.2 g), rounded to a practical amount. IBU/ABV/SRM formulas stay
   forbidden. Eval row added (naming S-04 — a history-free "this yeast"
   cannot be resolved and should ask a clarifying question).

---

### 6.3 Dialogue history (decided 2026-11-22)

Normal use is a dialogue: discuss a style, then ask "Which yeasts might be
good for this style?". Two layers make that work:

- Generation: the LLM sees recent turns (max_history_turns + token budget,
  app.py passes full history; caption shows "history: N of M turns").
- Retrieval: `expand_query` re-injects the last MAX_QUERY_TURNS (3) USER
  turns into the retrieval query, so deep anaphora resolves ("this style"
  named 2+ turns back, with anaphoric turns in between). Assistant turns
  are excluded on purpose: their wording biases retrieval toward what was
  already shown. Part-number pinning fires on codes from any injected turn.
- Short selector follow-ups (<30 chars, "The BJCP one.") retrieve by the
  last user turn alone (replaced, not appended).
- Tests: tests/test_expand_query.py (offline). Verified live: 3-turn
  saison dialogue retrieves 8/8 saison yeasts on every turn.

---

## 7. Recommended Immediate Next Steps

1. Install Ollama and pull a 7B model (e.g., `llama3.1:8b` or `qwen2.5:7b`) to verify it runs on the RTX 3060.
2. Gather initial data: 1–2 public BeerXML recipe files + BJCP style guide text.
3. Decide frontend: Streamlit vs. Gradio.
4. Decide RAG framework: LangChain vs. LlamaIndex.
5. Scaffold initial `app.py` + ingestion script.

---

## 8. Open Questions (Do Not Assume Answers)

- [ ] Streamlit vs. Gradio for the frontend?
- [ ] LangChain vs. LlamaIndex for RAG orchestration?
- [ ] Which 7B model to start with?
- [ ] Exact public data sources for BeerXML recipes and hop/malt references?

Resolve these with the user before implementing.

---

## 9. File Layout (Expected, Not Yet Enforced)

```
brew-assistant/
├── AGENTS.md                 # this file
├── BREW_BOT_PLAN.md          # full project plan
├── app.py                    # Streamlit chat UI (to be created)
├── ingest.py                 # data ingestion → ChromaDB (to be created)
├── config.yaml / .env        # model name, temperature, paths (to be created)
├── data/                     # BeerXML, text, CSV sources
├── chroma_db/                # persisted vector store
└── tests/                    # test questions + pass/fail tracking
```

---

## 10. How to Verify You Understand the Project

Before making changes, you should be able to answer:
- Where is the full plan stored? → `BREW_BOT_PLAN.md`
- How is the LLM served? → Ollama (local)
- Where do embeddings live? → ChromaDB
- Can the model be swapped without code changes? → Yes, via config
- Is this a cloud or local application? → Local only
- What is the MVP feature set? → Chat + styles + substitutions + recipe help + citations
