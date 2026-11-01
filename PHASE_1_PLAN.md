# Phase 1 — Prototype Plan

**Status:** Ready to start
**Parent plan:** `BREW_BOT_PLAN.md` (source of truth)
**Estimate:** 1–2 weeks (likely less since decisions are pre-resolved)

---

## 1. Locked Decisions (resolved with user)

| Decision | Choice |
|----------|--------|
| Frontend | **Streamlit** |
| RAG framework | **LangChain** (with `langchain-ollama` + `langchain-chroma`) |
| Starting model | **qwen2.5:7b** via Ollama (4-bit quantized, fits RTX 3060 12 GB) |
| Phase 1 data | **Generated docs** — ~20 beer style + ingredient files authored from brewing knowledge. Real public sources (BJCP, hop tables) deferred to Phase 2 |
| Embeddings | `all-MiniLM-L6-v2` via sentence-transformers (CPU is fine; ~90 MB download once) |

---

## 2. Definition of Done

1. `python ingest.py` builds a persisted ChromaDB at `./chroma_db/` with ~20 chunks and prints the count.
2. `streamlit run app.py` opens a working chat UI.
3. Asking **"Can I substitute Cascade hops?"** returns a sensible answer (e.g., Centennial / Amarillo / Citra) **with source citations**.
4. **Model swap test:** change the model name in config only → a different Ollama model answers. No code changes.
5. "Show sources" toggle displays the retrieved chunks for a given answer.
6. A small CSV of 10–20 eval questions exists in `tests/` with expected keywords and a pass/fail column filled in from a manual run.

---

## 3. Project Structure (to create)

```
brew-assistant/
├── AGENTS.md
├── BREW_BOT_PLAN.md
├── PHASE_1_PLAN.md          # this file
├── app.py                   # Streamlit chat UI
├── rag.py                   # retrieval + generation chain (shared logic)
├── ingest.py                # data/ → chunks → embeddings → ChromaDB
├── config.yaml              # model name, temperature, top_k, paths
├── requirements.txt         # pinned versions
├── .gitignore               # chroma_db/, .venv/, __pycache__/
├── data/
│   ├── styles/              # ~10 beer style docs (.md)
│   └── ingredients/         # ~10 hop/malt/yeast docs (.md)
├── chroma_db/               # persisted vector store (gitignored)
└── tests/
    └── eval_questions.csv   # question, expected keywords, pass/fail, notes
```

**Config rules (per AGENTS.md):** the Ollama model name lives only in `config.yaml` — never hardcoded in business logic.

---

## 4. Task Breakdown

### Step 1 — Environment setup
- Verify Python 3.11+ (`python --version`); create `.venv`.
- Install Ollama if missing; `ollama pull qwen2.5:7b` (~4.7 GB download).
- Verify GPU offload: `ollama run qwen2.5:7b` then check `nvidia-smi` shows the model in VRAM.
- Install packages into venv:
  ```
  streamlit langchain langchain-ollama langchain-chroma
  langchain-huggingface sentence-transformers chromadb pyyaml
  ```

### Step 2 — Project skeleton
- Create the directory tree above.
- Write `requirements.txt` (pinned — LangChain's split packages churn quickly).
- Write `config.yaml`: model name (`qwen2.5:7b`), temperature, top_k, data dir, chroma path.
- Write `.gitignore`: `chroma_db/`, `.venv/`, `__pycache__/`, `*.pyc`.

### Step 3 — Author ~20 docs in `data/`
- **`data/styles/` (~10):** American IPA, NEIPA, American Pale Ale, German Hefeweizen, Czech Pilsner, Dry Stout, Porter, Belgian Saison, Amber Ale, Barleywine. Each: BJCP-style description, key stats (OG/FG/ABV/IBU/SRM ranges), characteristic ingredients.
- **`data/ingredients/` (~10):** Hops — Cascade, Citra, Centennial, Saaz, Mosaic, Fuggle (alpha acid %, flavor/aroma, common substitutions). Malt — Pale 2-row, Munich, Roasted Barley. Yeast — a neutral American ale strain and a Hefeweizen strain (attenuation, flocculation, flavor).
- Include at least 2–3 documents that explicitly mention substitution relationships so the verification question has grounding.

### Step 4 — Ingestion script (`ingest.py`)
- Load all `.md`/`.txt` from `data/`.
- Chunk **by entity**: docs are small, so one doc ≈ one chunk (split only if a doc exceeds ~800 tokens).
- Embed with `HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")` (from `langchain-huggingface`).
- Store in `Chroma` persisted at `./chroma_db/` with metadata: `source` (filename), `type` (style/hop/malt/yeast).
- Print total chunk count; make the script idempotent (drop/rebuild collection on re-run).

### Step 5 — RAG chain (`rag.py`)
- `ChatOllama(model=config.model, temperature=config.temperature)` — model name from config only.
- Retriever: Chroma similarity search, top_k from config.
- System prompt: brewing assistant persona; uses only retrieved context; **cites sources by filename**; says when it doesn't know rather than hallucinating IBU/ABV numbers.
- Streaming response (token generator for Streamlit).
- Expose `get_retrieved_sources(query)` so the UI can show retrieved chunks.

### Step 6 — Streamlit app (`app.py`)
- `st.chat_input` / `st.chat_message` with streaming into `st.session_state`.
- Sidebar:
  - Model dropdown (populated from `ollama list` via Ollama's REST API, with the config default preselected)
  - Temperature slider
  - Top-K slider
  - "Re-ingest data" button (runs `ingest.py` logic, shows progress)
  - "Show sources" toggle → renders retrieved chunks + citations under each answer

### Step 7 — Evaluation + verification
- Create `tests/eval_questions.csv`: columns `question, expected_keywords, actual_excerpt, pass_fail, notes`.
- Seed with 10–15 questions, including:
  - "Can I substitute Cascade hops?" → expected: Centennial/Amarillo/Citra (must PASS)
  - Style questions (e.g., "What is a typical OG for a Czech Pilsner?")
  - Yeast question (e.g., "Which yeast for a Hefeweizen?")
  - Adversarial: "What should I brew today?" / "Calculate the IBU of this recipe" → expected: graceful fallback, no hallucinated numbers
- Run all questions manually once; record pass/fail.
- Perform the model-swap test (Definition of Done #4).

---

## 5. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| LangChain API churn (`langchain-community` splits) | Use the dedicated split packages (`langchain-ollama`, `langchain-chroma`, `langchain-huggingface`); pin versions in `requirements.txt` |
| Ollama model not using GPU | Verify with `nvidia-smi` during generation in Step 1; RTX 3060 has plenty of headroom for a 7B quantized model |
| One-time internet downloads | `qwen2.5:7b` (~4.7 GB) and `all-MiniLM-L6-v2` (~90 MB) download once; app is fully local afterward (local-first rule preserved) |
| Hallucinated brewing numbers | System prompt forbids inventing stats without context; adversarial eval questions in Step 7 |
| Streamlit streaming + session state bugs | Use `st.write_stream` and keep chat history as plain message dicts in `st.session_state` |

---

## 6. Out of Scope for Phase 1 (deferred)

- BeerXML parsing, BJCP guideline ingestion, CSV reference tables → **Phase 2**
- Formal test pass/fail tracking UI → **Phase 3**
- Recipe calculator, batch scaling, off-flavor troubleshooting, fine-tuning → **Phase 4**
