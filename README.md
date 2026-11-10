# 🍺 Brew Assistant

A **local-first chat application** for home brewers: ask about beer styles, hop/malt/yeast
substitutions, BJCP guidelines, or recipes, and get answers **grounded in a local document
library with inline source citations**.

Retrieval runs on ChromaDB + `sentence-transformers`; generation runs on a local LLM served by
[Ollama](https://ollama.com). No cloud APIs, no telemetry — documents and models stay on your
machine.

> Project plan and conventions: [`BREW_BOT_PLAN.md`](BREW_BOT_PLAN.md) · [`AGENTS.md`](AGENTS.md).
> Phase plans: [`PHASE_1_PLAN.md`](PHASE_1_PLAN.md), [`PHASE_2_PLAN.md`](PHASE_2_PLAN.md).

---

## Features

- 💬 Streamlit chat with streaming answers
- 📚 RAG over styles, BJCP guidelines, hops, malts, yeasts, recipes, and reference tables
- 🔎 **Search scope** filter by document type (sidebar multiselect)
- 🤖 **Model selector** in the sidebar — a curated candidate list from `config.yaml`
  (`models:`), or every model pulled into Ollama; labels unpulled candidates
- 📝 **Inline citations** in answers (`[source: path/to/doc.md]`) plus a collapsible sources panel
- 🏷️ Per-answer **model provenance** (`model · T=…`) and a notice when switching models mid-chat
- ⚙️ Temperature and Top-K controls, re-ingest button; per-model overrides (`temperature`, `num_ctx`)
- ✅ 23-question evaluation suite with keyword scoring and a citation audit

---

## Requirements

| Requirement | Notes |
|---|---|
| **Python 3.11+** | Tested with 3.13 |
| **Ollama** | Running locally at `http://localhost:11434` |
| **Disk** | ~4–5 GB for a 7B quantized model; ~90 MB for the embedding model |
| **GPU** | Optional. NVIDIA RTX 3060 (12 GB) tested; a 7B quantized model runs fully on GPU |
| **OS** | Windows tested; macOS/Linux should work (only the venv activation command differs) |

The pinned `torch` build is **CPU-only** (`torch==2.15.0+cpu`), so embeddings run on CPU — that is
intentional. The LLM runs on the GPU via Ollama.

---

## Quick start

```powershell
# 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate

# 2. Install dependencies (fully pinned)
pip install -r requirements.txt

# 3. Pull a chat model (the app defaults to qwen2.5:7b)
ollama pull qwen2.5:7b

# 4. Build the vector store from the repo fixtures
python ingest.py

# 5. Start the chat UI
streamlit run app.py
```

The app works at step 4 using only the repo fixtures in `data/` (10 styles, 10 ingredient docs,
2 sample recipes, 2 CSV reference tables). Run the collectors first if you want the full library:

```powershell
python -m collectors     # optional: downloads the bulk data into collected_data/
python ingest.py         # then (re)build the store
```

If PowerShell blocks the activation script:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

---

## How it works

```
Question
   │
   ▼
ChromaDB similarity search (top_k chunks, optional type filter)
   │  embeddings: all-MiniLM-L6-v2 (CPU)
   ▼
Prompt: system rules + chat history + retrieved context blocks
   │
   ▼
Ollama chat model (streaming)
   │
   ▼
Answer with [source: …] citations + retrieved-sources panel
```

Documents are chunked **by entity** (one hop/malt/yeast/style per chunk, one recipe per chunk,
one table row per chunk). Recipes, BJCP styles, and table rows are never split; an oversized
one of those entities is capped at 2,600 characters with an explicit truncation marker.

---

## Configuration

All settings live in [`config.yaml`](config.yaml). Relative paths resolve against the project
root; absolute paths (e.g. a UNC share) are used as-is.

| Key | Default | Purpose |
|---|---|---|
| `model` | `qwen2.5:7b` | Ollama model name. **The only place the model is configured.** |
| `models` | absent | Chat candidates for the sidebar dropdown: a map of model name → optional per-model overrides (`temperature`, `num_ctx`). Row order = dropdown order. Absent → dropdown lists every pulled model. |
| `temperature` | `0.3` | Generation temperature (sidebar slider overrides per session) |
| `top_k` | `6` | Chunks retrieved per question (sidebar slider overrides per session) |
| `num_ctx` | `8192` | Ollama context window — fits `top_k` chunks plus chat history |
| `data_dir` | `data` | Legacy single directory; ingest uses `data_dirs` |
| `data_dirs` | `[data, collected_data]` | Directories ingest reads from; missing ones are skipped with a warning |
| `collect_dest` | `collected_data` | Where collectors write (`<collect_dest>/<collector_id>/`) |
| `collectors` | `[diydog, bjcp, whitelabs, wyeast, brewingtools]` | Collectors run by `python -m collectors` |
| `chroma_dir` | `chroma_db` | Persisted vector store (gitignored) |
| `embedding_model` | `all-MiniLM-L6-v2` | sentence-transformers embedding model |

### Sidebar controls

- **Ollama model** — populated from the `models:` shortlist in `config.yaml` when present
  (pulled candidates first, unpulled ones labeled `(not installed)`); otherwise from Ollama's
  `/api/tags` with the configured default preselected. The choice applies to the current
  session only.
- **Temperature** — each model gets a fresh slider defaulting to its effective temperature
  (per-model override, else global); the slider overrides per session.
- **Top-K documents** — per-session override of the config value.
- **Search scope** — only retrieve the selected document types. Leave it empty to search
  everything. Counts per type are shown next to each label.
- **Show sources** — toggle the retrieved-chunk panel under each answer.
- **Re-ingest data** — rebuilds the vector store from the current `data_dirs`.

---

## Adding or swapping a model

The model name lives **only** in `config.yaml`. Swapping models requires no code changes.

1. Pull the model into Ollama:

   ```powershell
   ollama pull llama3.1:8b        # or qwen2.5:7b, mistral:7b, …
   ollama list                    # confirm the exact name
   ```

2. Set it in `config.yaml`:

   ```yaml
   model: llama3.1:8b
   ```

3. Restart the app (`streamlit run app.py`). The sidebar dropdown lists the configured model
   and every other Ollama model.

### Curating the candidate list

To make model comparison easy, first fill in the candidate list in `config.yaml` — the
dropdown then shows exactly these models (pulled ones first, unpulled ones labeled
`(not installed)`):

```yaml
model: qwen2.5:7b
models:
  qwen2.5:7b:   { num_ctx: 8192 }
  gemma4:12b:   {}
  qwen3.5:9b:   {}
  mistral:7b:   { temperature: 0.2 }   # optional per-model overrides
```

Each key's value may override `temperature` and `num_ctx` for that model; anything not
overridden falls through to the global setting. Delete the `models:` key to go back to
"every model pulled into Ollama". Every answer in the chat records the model that produced
it (`model: … · T=…` under the answer), and switching models mid-conversation flags it —
useful when comparing candidate models side by side.

4. Verify that the configured model is actually serving answers:

   ```powershell
   .venv\Scripts\python.exe tests\verify_model_swap.py
   ```

   It asks the verification question and asserts via Ollama's `/api/ps` that the configured
   model is loaded.

**Notes**

- Pick a **chat** model. Embedding-only models (e.g. `nomic-embed-text`) will appear in the
  dropdown but cannot answer questions.
- `num_ctx: 8192` is sized for a 7B model on a 12 GB GPU. If you move to a larger model, raise it
  only if the GPU has headroom — otherwise Ollama may spill to CPU and slow down.
- Check GPU residency with `ollama ps` while generating.

---

## Adding data

Two layers exist:

- `data/` — **repo fixtures only** (self-authored docs, sample recipes, reference tables). Safe
  to commit and used by the tests.
- `collected_data/` — **bulk/third-party output** from collectors. Gitignored; never commit it.

### Run the built-in collectors

```powershell
python -m collectors                      # every collector listed in config.yaml
python -m collectors --only bjcp,diydog   # a subset
python -m collectors --dest D:\brew-data  # override collect_dest
```

Statuses: `OK` (files written), `SKIP` (source unreachable — fail-soft), `FAIL` (unexpected
error), `MISSING` (configured but no module). Exit code is non-zero only for `FAIL`.

| Collector id | Source | Writes under `collect_dest/<id>/` |
|---|---|---|
| `diydog` | [stuartraetaylor/diydog-beerxml](https://github.com/stuartraetaylor/diydog-beerxml) | `recipes/*.xml` |
| `bjcp` | BJCP 2021 style exports | `bjcp/*.md` (one style per file) |
| `whitelabs` | whitelabs.com public strain pages | `ingredients/yeast-*.md` |
| `wyeast` | wyeast.com public strain pages | `ingredients/yeast-*.md` |
| `brewingtools` | [brewing.tools](https://brewing.tools/charts) open ingredient DB (ODbL 1.0) | `ingredients/hop-*.md`, `ingredients/malt-*.md` |

Each collector owns only its own namespace (e.g. `collected_data/bjcp/`), so re-running one never
touches another collector's files or files you placed by hand. Re-running a collector replaces
its own subtree.

### Add a new collector

One source = one class = one file. Nothing in `ingest.py`, `rag.py`, or `app.py` changes.

1. Create `collectors/<resource>_collector.py`.
2. Subclass `DataCollector`; set `id = "<resource>"` and `description`.
3. Implement `collect(self, dest) -> list[Path]`. `dest` is your namespaced directory; write
   canonical files only:
   - `recipes/*.xml` — BeerXML
   - `bjcp/*.md` — one style per file
   - `tables/*.csv` — reference tables
   - `ingredients/{hop,malt,yeast}-*.md`
   - `styles/*.md`
4. Decorate the class with `@register`.
5. Add the id to `collectors:` in `config.yaml`.

Rules: be idempotent (clear your own subtree first), fail soft with `SourceUnavailableError` when
the source is unreachable, stay inside `dest`, and never write raw HTML (normalize to markdown,
see `collectors/web.py`). Full details: [`collectors/base.py`](collectors/base.py) docstring.

### Add your own documents

Point `data_dirs` at any folder and re-ingest — no collector needed:

```yaml
data_dirs:
  - data
  - collected_data
  - \\myshare\brew\personal
```

Ingest understands `.md` / `.txt` (one document per file), `.xml` BeerXML (one document per
`<RECIPE>`), and `.csv` (one document per row). Document type is inferred from the type-directory
name (`styles/`, `recipes/`, `bjcp/`, `tables/`) or from the `hop-*` / `malt-*` / `yeast-*`
filename prefix.

After changing `data_dirs`, rebuild the store with `python ingest.py` or the sidebar
**Re-ingest data** button.

---

## Evaluation and tests

### Evaluation suite

[`tests/eval_questions.csv`](tests/eval_questions.csv) holds 23 questions (13 Phase 1 regression
questions + 10 Phase 2 questions).

```powershell
.venv\Scripts\python.exe tests\run_eval.py                  # ask the model, score, audit citations
.venv\Scripts\python.exe tests\run_eval.py --retrieval-only # top-k sources only, no LLM call
```

Each run writes the answer excerpt and verdict back into the CSV (`actual_excerpt`, `pass_fail`),
so the file always shows the latest baseline. Rows marked `MANUAL` are skipped and need human
review.

CSV columns: `question, expected_keywords, actual_excerpt, pass_fail, notes`.

`expected_keywords` syntax (case-insensitive substring):

- `;` separates **required groups** (all must match)
- `|` separates **alternatives** within a group (any one matches)
- `MANUAL` — not scored automatically

The runner also audits every `[source: …]` citation and reports per row and in total:

| Verdict | Meaning |
|---|---|
| `OK` | The path exists in `data_dirs` and was retrieved this turn |
| `ABBREVIATED` | Path missing on disk but a unique suffix of a retrieved path (e.g. dropping a collector folder) |
| `PHANTOM` | Fabricated — not on disk and not a retrieved document |
| `UNGROUNDED` | Exists on disk but was not retrieved this turn |

The citation audit is console-only and does not change pass/fail scoring.

### Smoke tests

| Script | What it covers | Needs |
|---|---|---|
| `tests/smoke_ingest.py` | Ingestion over repo fixtures, per-type counts, contract shape | Nothing (offline) |
| `tests/smoke_retrieval.py` | Store counts, type filter, source shape; exits 0 with `SKIP` if not ingested | Ingested store |
| `tests/smoke_rag.py` | End-to-end answer + scoped retrieval for the Cascade question | Ollama + ingested store |
| `tests/smoke_collectors.py` | Registry, namespace rule, runner statuses, HTML→markdown, archive extraction | Nothing (offline, no real sources) |
| `tests/test_model_selection.py` | `models:` merge, dropdown candidates, switch detection, `get_llm` precedence | Nothing (offline, no Ollama) |
| `tests/verify_model_swap.py` | Config-only model swap is actually served | Ollama + ingested store |

---

## Project layout

```
brew-assistant/
├── app.py                  # Streamlit chat UI
├── rag.py                  # retrieval, prompt, model, streaming
├── ingest.py               # data_dirs → chunks → embeddings → ChromaDB
├── config.py               # config.yaml loader (resolves paths)
├── config.yaml             # model, temperature, top_k, num_ctx, data dirs, collectors
├── collectors/             # one collector class per data source
│   ├── base.py             # DataCollector ABC + registry + "how to add" docs
│   ├── __main__.py         # python -m collectors [--only] [--dest]
│   ├── runner.py  web.py
│   └── {diydog,bjcp,whitelabs,wyeast,brewingtools}_collector.py
├── data/                   # committed fixtures only
│   ├── styles/  ingredients/  recipes/  tables/
├── collected_data/         # gitignored collector output
├── chroma_db/              # gitignored persisted vector store
├── tests/                  # eval CSV + runner + smoke tests
├── BREW_BOT_PLAN.md        # source of truth for scope/roadmap
├── AGENTS.md               # conventions for contributors/agents
└── requirements.txt
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Sidebar lists only unpulled candidates as `(not installed)` | Those `models:` entries are not pulled in Ollama yet: `ollama pull <name>` (check the exact tag with `ollama list`). |
| Sidebar lists only the configured model | Ollama is not reachable at `http://localhost:11434`. Start it (`ollama serve`, or the Ollama app) and reload the page. |
| `Search scope: run ingestion to enable type filtering` | `chroma_db/` is empty or missing — run `python ingest.py`. |
| Answers ignore your new files | Re-ingest (`python ingest.py` or the sidebar button) after changing `data_dirs`. |
| First question is slow | The embedding model (~90 MB) downloads once on first use, and Ollama loads the model on first request. Subsequent turns are faster. |
| Model answers slowly / GPU idle | Check `ollama ps`; if the model reports CPU, reduce `num_ctx` or pick a smaller model. `nvidia-smi` confirms VRAM use. |
| `python -m collectors` prints `SKIP` | The upstream source is unreachable right now (network, site change). Fixtures still keep the app usable; retry later. |
| PowerShell refuses to activate the venv | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |

---

## Privacy and licensing

- Everything runs locally. The only network access is optional: pulling Ollama models, the
  one-time embedding-model download, and running collectors.
- No telemetry, no external model endpoints, no cloud vector store.
- Do not commit third-party or personal data. `collected_data/` is gitignored by design; repo
  `data/` holds small self-authored fixtures only.
- Collectors normalize data for personal use. Respect each source's license and terms — e.g. the
  brewing.tools ingredient database is ODbL 1.0.
