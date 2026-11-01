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
