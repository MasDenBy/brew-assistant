# Home Brewing Assistant — Project Plan

**Working directory:** `C:\Ihar\Projects\brew-assistant`  
**Status:** Planning / Phase 1 (Prototype)  
**Last updated:** Sun Nov 01 2026

---

## 1. Goal

Build a local chat application that helps home brewers with:
- Questions about brewing ingredients (hops, malts, yeasts, water, adjuncts)
- Ingredient substitutions (e.g., "I don't have Cascade hops, what can I use?")
- Recipe creation for beer (style guidelines, formulation help)

The system must use **open-source models** and **RAG** (Retrieval-Augmented Generation) because prompt instructions cannot hold enough brewing-specific data. The model must be **swappable** without rebuilding the application.

---

## 2. Constraints & Environment

| Constraint | Value |
|------------|-------|
| Deployment | Local machine only |
| RAM | 32 GB |
| GPU | NVIDIA RTX 3060, 12 GB VRAM, CUDA supported |
| Tech stack | Python |
| Frontend | To be assessed (Streamlit recommended) |
| Model class | 7B class open-source LLM (fits VRAM) |
| Privacy | Data stays on local machine |

---

## 3. Recommended Tech Stack

| Layer | Tool | Rationale |
|-------|------|-----------|
| Frontend | **Streamlit** (recommended) or Gradio | Pure Python, built-in chat UI (`st.chat_message`), no HTML/JS required |
| Backend / Orchestration | **LangChain** or **LlamaIndex** | Python-native RAG frameworks; LangChain has more Ollama examples |
| Model serving | **Ollama** (local) | Makes model swapping trivial; exposes local REST API; auto CUDA offloading |
| Embeddings | **sentence-transformers** (`all-MiniLM-L6-v2` or `BAAI/bge-small-en`) | Small, fast, CPU or GPU compatible |
| Vector store | **ChromaDB** | Python-native, local (SQLite + DuckDB), zero server setup, easy persist/reset |
| Data formats | BeerXML + plain text / Markdown / CSV | BeerXML for recipes; text/Markdown for style guides and ingredient references |

---

## 4. Architecture

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

**Key design principle:** The LLM is abstracted behind an interface (e.g., LangChain `ChatOllama`). Switching models is a configuration change, not a code change.

---

## 5. Components

### 5.1 Frontend (Streamlit)
- `app.py` with `st.chat_input` / `st.chat_message`
- Sidebar controls:
  - Model selector (dropdown to swap Ollama models)
  - Temperature / top-p sliders
  - "Re-ingest data" button
  - Toggle to show retrieved sources (transparency)
- MVP scope: chat input → RAG → answer + source citations

### 5.2 Backend (LangChain + Ollama)
- `ChatOllama` wrapper so the backend is model-agnostic
- Prompt template: system prompt defines brewing assistant persona; instructs to cite sources when using retrieved context
- Retrieval chain:
  1. User query → embedding → ChromaDB similarity search (top-K chunks)
  2. Retrieved chunks stuffed into prompt
  3. Ollama generates streamed answer

### 5.3 RAG Pipeline
**Ingestion (run once, re-run when data changes):**
1. Collect BeerXML files + ingredient/style text docs
2. Chunk by document section (one hop per chunk, one recipe per chunk, one style guideline per chunk)
3. Embed with sentence-transformers
4. Store in ChromaDB with metadata (`source`, `type`: hop/malt/yeast/recipe/style)

**Retrieval (per user query):**
1. Embed query
2. Search ChromaDB, return top 3–5 chunks
3. Optional metadata filter (e.g., hops only)

**Generation:**
1. Combine retrieved chunks + user query into prompt
2. Stream response from Ollama

---

## 6. Model Recommendations (7B class, fits RTX 3060 12 GB)

| Model | Size | Notes |
|-------|------|-------|
| Llama-3.1-8B-Instruct | 8B | Strong instruction following and reasoning |
| Qwen2.5-7B-Instruct | 7B | Excellent reasoning, often beats Llama at 7B |
| Mistral-7B-Instruct-v0.3 | 7B | Already tested by user; good baseline |
| Gemma-2-9B-IT | 9B | Fits quantized; already tested by user |

**Start with:** `llama3.1:8b` or `qwen2.5:7b` via Ollama (4-bit quantized by default ≈ 4–5 GB VRAM).

Ollama commands:
```bash
ollama pull llama3.1:8b
ollama pull qwen2.5:7b
ollama list
ollama run llama3.1:8b
```

**Model swapping strategy:** Change model name in config/`.env`; restart or call different model in API. No fine-tuning required to swap.

---

## 7. Data Sources (Public Brewing Knowledge)

- **BJCP Style Guidelines** — beer style definitions (PDF/text, publicly available)
- **BeerXML recipe repositories** — public collections on GitHub
- **Hop varieties** — Hopswiki, HopUnion, Yakima Chief (alpha acid, flavor profiles)
- **Yeast labs** — White Labs, Wyeast (attenuation, flocculation, flavor)
- **Malt data** — Briess, Weyermann, etc. (public spec sheets)
- **Hugging Face / Kaggle** — search "beer recipe" datasets

**MVP data start:** BJCP styles + a handful of BeerXML recipes + a hop substitution table (CSV). Expand incrementally.

---

## 8. Evaluation & Tuning

RAG tuning (not model fine-tuning in MVP):

1. **Retrieval quality** — manually test 10–20 brewing questions; verify relevant context appears in top-5 ChromaDB results; adjust chunk size, overlap, embedding model, or top-K
2. **Generation quality** — check for hallucinated IBU/ABV/SRM numbers; verify substitution logic is sensible (e.g., Cascade → Centennial OK; Cascade → chocolate malt not OK); adjust system prompt
3. **Optional later:** Fine-tune (LoRA) on brewing Q&A pairs if base model consistently struggles with terminology

**Evaluation artifact:** Simple CSV of test questions → expected keywords → actual answer → pass/fail. Later consider Ragas or TruLens.

---

## 9. Development Roadmap

### Phase 1 — Prototype (1–2 weeks)
- Install Python, Ollama, ChromaDB, sentence-transformers, LangChain, Streamlit
- Pull one 7B model to Ollama; verify it runs on RTX 3060
- Hardcode ~20 beer style + ingredient text docs
- Build ingestion → ChromaDB
- Build Streamlit chat (retrieve + generate)
- Verify: "Can I substitute Cascade hops?" returns a sourced answer

### Phase 2 — Data Expansion (1 week)
- Add BeerXML recipe collection
- Add hop/malt/yeast reference tables (CSV → text chunks)
- Add BJCP style guidelines
- Improve chunking by entity type

### Phase 3 — Polish (1 week)
- Model selector in UI sidebar
- Source citations in answers
- Basic test question set + pass/fail tracking
- Documentation on adding new models/data

### Phase 4 — Optional Enhancements (later)
- Recipe calculator (IBU, ABV, SRM) as tool/function call
- Batch size scaling
- Off-flavor troubleshooting
- Fine-tuning experiment

---

## 10. Immediate Next Steps

1. Install Ollama and test one 7B model locally to confirm it runs on the RTX 3060
2. Gather initial data: 1–2 public BeerXML recipe files and BJCP style guide text
3. Pick frontend: Streamlit (recommended) vs. Gradio
4. Pick RAG framework: LangChain vs. LlamaIndex
5. Draft initial `app.py` / ingestion script skeleton

---

## 11. Open Questions (to resolve during implementation)

- [x] Frontend: Streamlit vs. Gradio? → **Streamlit** (decided Sun Nov 01 2026)
- [x] RAG framework: LangChain vs. LlamaIndex? → **LangChain** (decided Sun Nov 01 2026)
- [x] Which 7B model to start with: Llama-3.1-8B vs. Qwen2.5-7B vs. Mistral-7B? → **Qwen2.5-7B** via Ollama (decided Sun Nov 01 2026)
- [ ] Exact public data sources for BeerXML recipes and hop/malt references? → deferred to Phase 2; Phase 1 uses generated docs (see `PHASE_1_PLAN.md`)
