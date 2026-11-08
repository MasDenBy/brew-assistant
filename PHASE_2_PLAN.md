# Phase 2 — Data Collection & Expansion Plan

**Status:** Draft v3 (decisions resolved with user; branch `phase-2`)
**Parent plan:** `BREW_BOT_PLAN.md` (source of truth — §9 "Phase 2 — Data Expansion")
**Estimate:** ~6–7 days

---

## 1. Design Principles (decided with user)

1. **Repo `data/` = fixtures only.** Small files needed for smoke tests and eval regression, plus the self-authored CSV reference tables (license-clean, needed by tests — decision §9.3). Nothing bulk, third-party, or personal in git.
2. **Bulk/third-party data lives outside git**, collected by a dedicated application into a configurable directory.
3. **Default destination is `collected_data/`** (gitignored) — easiest for development out of the box. Users point it wherever they want (e.g. `\\myshare\brew`) by changing config.
4. **`data_dirs` defaults to `[data, collected_data]`** — chat works immediately without running any collector (fixtures alone are enough to try it).
5. **One data collector = one class = one source** (e.g. `bjcp_collector.py`, `whitelabs_collector.py`). Adding a source = adding a collector class + enabling it in config — `ingest.py`, `rag.py`, `app.py` never change.

### Target flow

```
1. python -m collectors          # enabled collectors → files land in collect_dest (default: collected_data/)
2. python ingest.py              # reads data_dirs ([data, collected_data] by default) → ChromaDB
3. streamlit run app.py          # chat (works even at step 0, on fixtures alone)
```

---

## 2. Scope

1. **Collector application** (new): framework + runner + **five MVP collectors**:
   `diydog`, `bjcp`, `whitelabs`, `wyeast`, `brewingtools (hops + malts)`
2. **Ingestion reads configurable `data_dirs`** (default `[data, collected_data]`) instead of hardcoded `data/`
3. **New formats via canonical output:** BeerXML recipes, BJCP styles, lab/hop reference docs — collectors normalize to formats ingest understands (`.md` / `.txt` / `.xml` / `.csv`)
4. **Chunking by entity type** (one recipe/style/hop per chunk — AGENTS.md §6.6)
5. **Search-scope filter** by entity type (retrieval + sidebar)
6. **Default `top_k` raised 4 → 6** (config change; chunk count grows 5×+)
7. **Evaluation:** regression gate on the 13 Phase 1 questions + new Phase 2 questions

**Resolved from v1:** licensing is handled structurally — raw third-party files are never committed; collectors download to the (gitignored) destination.

**Explicitly deferred:** a `local_folder_collector` for the user's own files — noted in `BREW_BOT_PLAN.md` §11 for later design (users can already point `data_dirs` at any folder of `.md`/`.xml`/`.csv` files, which covers the basic case without a dedicated collector).

---

## 3. Architecture

### 3.1 Collector framework

```
collectors/
├── __init__.py          # registry: @register decorator + collector lookup
├── __main__.py          # runner: python -m collectors [--only bjcp,diydog]
├── base.py              # DataCollector ABC (+ "how to add a collector" docs)
├── web.py               # shared helpers: polite HTTP fetch, HTML→text/markdown
├── diydog_collector.py  # BrewDog DIY Dog BeerXML recipes → recipes/*.xml
├── bjcp_collector.py    # BJCP 2021 export → bjcp/*.md (one style per file)
├── whitelabs_collector.py  # White Labs strain pages → ingredients/yeast-*.md
├── wyeast_collector.py     # Wyeast strain pages → ingredients/yeast-*.md
└── brewingtools_collector.py   # brewing.tools hops + fermentables → hop-*/malt-*.md
```

```python
class DataCollector(ABC):
    id: str                 # e.g. "bjcp" — also the namespaced dir under collect_dest
    description: str
    def collect(self, dest: Path) -> list[Path]:
        """Fetch/normalize source → write canonical files under dest/<self.id>/.
        Idempotent: clears its own subtree first. Returns written files.
        Must fail soft (warn + skip) when the source is unreachable."""
```

- **Registration:** `@register` in `collectors/__init__.py`; runner iterates the registry filtered by the `collectors:` list in `config.yaml`.
- **Namespace rule:** each collector owns `collect_dest/<collector_id>/` — re-running one collector never touches another's files or manually placed files.
- **Output contract (collector ↔ ingest):** canonical formats only —
  `recipes/*.xml` (BeerXML), `bjcp/*.md`, `tables/*.csv`, `ingredients/*.md`, `styles/*.md`.
  Type inference extends Phase 1's convention: type from the *type-directory name* at any depth (`recipes/`, `bjcp/`, `tables/`, `styles/`) or the filename prefix (`hop-*`, `malt-*`, `yeast-*`).
- **Polite fetching (web collectors):** small request volume (one-time, a few dozen pages max), timeout + retry with backoff, descriptive user agent, no hammering; HTML parsed to readable markdown (never commit raw HTML).

### 3.2 Config (`config.yaml`) — updated for Phase 2

```yaml
model: qwen2.5:7b
temperature: 0.3
top_k: 6                                # raised from 4 (more chunks in the store)
data_dir: data                          # (kept for reference/tests; ingest uses data_dirs)
data_dirs:                              # where ingest reads from
  - data                                # repo fixtures (+ self-authored CSV tables)
  - collected_data                      # gitignored collector output; chat works without it
collect_dest: collected_data            # where collectors write (default for dev;
                                        # user may point elsewhere, e.g. \\myshare\brew)
collectors: [diydog, bjcp, whitelabs, wyeast, brewingtools]
chroma_dir: chroma_db
embedding_model: all-MiniLM-L6-v2
```

### 3.3 Ingestion changes

- `load_documents()` iterates `cfg["data_dirs"]` (skip missing dirs with a warning — `collected_data/` may not exist yet).
- `infer_type()` extended for type-directories (`recipes` → `recipe`, `bjcp` → `bjcp_style`, `tables` → CSV `type` column or filename).
- No knowledge of collectors — ingest only reads files; the two apps are decoupled through the **file-format contract**.

---

## 4. Definition of Done

1. `python -m collectors` runs the five enabled collectors, writes canonical files under `collect_dest/<id>/`, prints a per-collector summary, and skips gracefully (warning) when a source is unreachable.
2. Repo `data/` contains **only** fixtures: the 20 Phase 1 docs, 1–2 hand-written sample BeerXML recipes, and the self-authored CSV tables — chat + all tests run with zero network.
3. `python ingest.py` works with default config (fixtures present, `collected_data/` missing or empty) and additionally ingests collected data when present; prints per-type counts (`style/hop/malt/yeast/recipe/bjcp_style/table`).
4. Recipe question → real recipe chunk with citation; BJCP question → guideline numbers with citation; yeast/hop questions → lab/variety docs with citation.
5. Sidebar **Search scope** filter (entity types) applied to retrieval.
6. Eval: all 13 Phase 1 questions still PASS; ≥8 new Phase 2 questions added and passing.
7. Model swap still config-only (regression).
8. **Extension documented:** "add a source" = subclass `DataCollector` + `@register` + add id to config (documented in `collectors/base.py`).

---

## 5. Collector Catalog

| Collector | Source | Output under `collect_dest/<id>/` | Fetch type |
|-----------|--------|-----------------------------------|------------|
| `diydog_collector` | [stuartraetaylor/diydog-beerxml](https://github.com/stuartraetaylor/diydog-beerxml) (~200 recipes) | `recipes/*.xml` | archive download |
| `bjcp_collector` | [bjcp.org downloads](https://www.bjcp.org/beer-styles/downloads-and-resources) — 2021 community JSON/XML exports | `bjcp/*.md` (one style per file) | download + convert |
| `whitelabs_collector` | whitelabs.com public strain pages | `ingredients/yeast-*.md` | web (polite) |
| `wyeast_collector` | wyeast.com public strain pages | `ingredients/yeast-*.md` | web (polite) |
| `brewingtools_collector` | [brewing.tools open ingredient database](https://brewing.tools/charts) — `/data/hops.json` (123) + `/data/fermentables.json` (383; 2 extracts excluded → 381 malts/adjuncts), ODbL 1.0 | `ingredients/hop-*.md`, `ingredients/malt-*.md` | JSON download |

**Repo fixtures (committed, in `data/`):** the 20 Phase 1 docs (they double as eval grounding), 1–2 hand-written sample `.xml` recipes, and the self-authored CSV reference tables (substitutions, alpha acids, yeast/malt comparisons — decision §9.3).

---

## 6. Target Structure

```
brew-assistant/
├── PHASE_2_PLAN.md
├── config.yaml                # + data_dirs, collect_dest, collectors; top_k=6
├── collectors/                # NEW — collector application (see §3.1)
│   ├── base.py  __main__.py  web.py
│   ├── diydog_collector.py  bjcp_collector.py
│   ├── whitelabs_collector.py  wyeast_collector.py  brewingtools_collector.py
├── collected_data/            # gitignored — collector output (default collect_dest)
├── data/                      # FIXTURES ONLY (+ self-authored CSV tables)
│   ├── styles/  ingredients/  # Phase 1 docs (kept)
│   ├── recipes/               # 1-2 hand-written sample .xml
│   └── tables/                # self-authored .csv reference tables
├── ingest.py                  # + data_dirs iteration, extended type inference
├── rag.py                     # + optional type filter
├── app.py                     # + sidebar search scope
└── tests/
    ├── eval_questions.csv     # + ~8-10 rows
    ├── smoke_ingest.py        # NEW: contract test — ingest fixtures end-to-end, offline
    ├── smoke_retrieval.py     # kept green
    └── smoke_rag.py           # kept green
```

`.gitignore` additions: `collected_data/`.

---

## 7. Task Breakdown

### Step 1 — Collector framework (~0.5 day)
- `collectors/base.py`: `DataCollector` ABC (`id`, `description`, `collect(dest)`), `@register` registry, extension docs.
- `collectors/__main__.py`: reads config, runs enabled collectors in order, per-collector success/skip/fail summary, `--only` flag.
- `collectors/web.py`: polite HTTP fetch helper (timeout, retry/backoff, user agent) + HTML→markdown helper.
- Config keys `collect_dest`, `data_dirs`, `collectors`; `.gitignore` += `collected_data/`; `top_k: 6`.

### Step 2 — Ingestion: multi-dir + contract test (~0.5 day)
- `data_dirs` iteration with missing-dir warning; extended `infer_type()` (`recipes/`, `bjcp/`, `tables/` + CSV `type` column).
- **Fixtures:** add 1–2 hand-written sample BeerXML recipes under `data/recipes/`.
- New `tests/smoke_ingest.py`: ingestion over fixtures only (offline) — asserts per-type counts and one chunk per recipe; pins the collector↔ingest contract.

### Step 3 — diydog collector (~0.5 day)
- Download repo archive → extract `*.xml` → `recipes/`; license note in the collector docstring; fail-soft on network errors.

### Step 4 — bjcp collector (~1 day)
- Download the 2021 community JSON/XML export → render one `.md` per style (code, name, vital-stat ranges, short impression excerpt) → `bjcp/`.
- Raw downloads stay in the collector's dest area — never committed.

### Step 5 — Lab, hop & malt collectors (~1.5 days)
- `whitelabs_collector`, `wyeast_collector`: iterate public strain pages → normalize (name, attenuation, flocculation, flavor profile, temperature) → `ingredients/yeast-<slug>.md`.
- `brewingtools_collector`: one run downloads both JSONs of the brewing.tools open ingredient database (same dataset version 0.7.0, ODbL 1.0): `/data/hops.json` (123 varieties) and `/data/fermentables.json` (383 records; 2 liquid extracts excluded by decision → 381 malts/adjuncts).
  - Hops normalized as before (alpha/beta acids, cohumulone, oils, aroma, substitutes/pairings, provenance) → `ingredients/hop-<id>.md`.
  - Malts normalized → `ingredients/malt-<id>.md`: colour °L and EBC both exactly as published (never converted), extract fine grind, moisture, max in batch, protein/total-nitrogen/soluble-nitrogen figures, diastatic power (Lintner and/or Windisch-Kolbach, either or both), aliases, grain group, description/notes profile, provenance.
  - Known gaps, accepted: 252/381 rendered malts have no description/notes (specs still render); the dataset has no substitutes, no pairings and no PPG field, so none are invented; extracts are excluded.
  - No scraping and no PDF extraction. All-or-nothing: if either endpoint fails or is restructured, the collector SKIPs and leaves previous files untouched.
  - HopUnion is gone as a public reference and beerMaverick is behind a Cloudflare managed challenge (HTTP 403 `Cf-Mitigated: challenge` for every path), so neither is used.
- Shared `web.py` keeps the web collectors thin; all fail-soft when sources are unreachable or restructured (see §8).

### Step 6 — Chunking by entity type (~0.5 day)
- Per-type strategy: `recipe`, `bjcp_style`, table rows → no split (entity = chunk, length guard); `.md` → existing splitter only when oversized.
- Length guard: never-split entities are capped at **2600 chars** (`MAX_ENTITY_CHARS`). Cut prefers the last `\n## ` section boundary in the second half of the budget (whole sections kept), else the last newline; the truncation marker tells the model the document continues. Capped chunks carry `truncated: True` + `chars: <original length>`; ingest summary reports `capped=… / longest=…`.
  - Measured on the 1,285-doc store: **75/110 BJCP docs capped**, longest chunk 2,592, recipes/hops/malts/yeasts untouched. Data-driven: the embedder (`all-MiniLM-L6-v2`, 256 tokens) only sees ~the first 1,000 chars anyway.
  - Accepted loss (Step 8 eval context): capped BJCP docs lose their tail — all 75 capped docs drop `Examples`, while `Flavor` survives in 68 and `Comparison with other styles` in 52 of them; no current eval row depends on truncated content.
- Recipe renderer fixes (BeerXML conformance): `AMOUNT` is **kg** → rendered `4.38 kg` (grams below 1 kg: `0.02 kg → 20 g`) instead of the old 1000×-off `g`; zero placeholders (`OG 0.0`, `FG 0.0`, `(0)` style number, zero hop time/colour/attenuation) are suppressed; notes are reduced to the first useful paragraph, stripped of `Contributed by:` / `PunkAPI id:` lines, and capped at 400 chars; full fermentable + hop schedule kept (guard is the backstop).
- Prompt budget: `config.yaml: num_ctx: 8192`, passed to `ChatOllama` by `rag.get_llm()` — 6 × 2600-char chunks fit with history headroom. Verified `qwen2.5:7b` stays 100% GPU-resident (`ollama ps`: 5.1 GB, context 8192).
- Fixtures: `data/recipes/sample-*.xml` converted to conformant kg amounts; contract assertions updated.
- Verification: 1,285 chunks / 0 splits / `capped=75` / `longest=2592`; smoke_ingest, smoke_retrieval, smoke_rag green; `run_eval.py` unchanged at 11 pass / 0 fail / 2 manual.

### Step 7 — Retrieval filter + UI (~0.5 day)
- `rag.retrieve(query, k, types=None)` → Chroma filter `{"type": {"$in": types}}`; thread through `get_retrieved_sources`.
- `app.py` sidebar: **Search scope** multiselect (default: all types).

Verification:
- Store unchanged at 1,285 chunks; scope list derived at runtime from Chroma metadata (`type_counts()`): `style 10, bjcp_style 110, hop 133, malt 385, yeast 315, recipe 328, table 4` — 7 entries in `TYPE_ORDER` order (`ingredient` absent: no chunks of that type).
- Measured on 1,285 rows: metadata scan ~25–35 ms (cached per process, so Streamlit reruns do not rescan); filtered query `{"type": {"$in": ["yeast"]}}` ~10 ms vs ~8 ms unfiltered.
- Single retrieval per turn: one filtered `retrieve()` feeds both the prompt context and the sources panel (`stream_answer(..., docs=docs)`), so the Top-K slider now controls answer context as well as the panel; the old config-`top_k` double retrieval is gone. Re-ingest calls `reset_caches()` (store + type counts).
- `smoke_retrieval.py` rewritten as assertions: type/count table + `PASS` for unfiltered k=6, `["hop"]`, `["hop","malt"]`, `["bjcp_style"]`, unknown type → `[]`, empty scope == unfiltered, source-dict shape; prints `SKIP: store not ingested` and exits 0 on a fresh clone. `smoke_rag.py` adds `ask(..., types=["hop"])` → answer non-empty and every source `hop`.
- `smoke_ingest.py`, `smoke_collectors.py`, `verify_model_swap.py` green; `run_eval.py` unchanged at **11 pass / 0 fail / 2 manual** (default path takes no filter).
- UI via Streamlit `AppTest`: 7 scope entries with counts, all selected by default; BJCP-only scope → `bjcp/…` sources only; cleared selection searches all types (mixed hop+recipe sources); Top-K 1 → 1 source, 6 → 6; re-ingest succeeds and the counts re-render; empty store hides the multiselect and shows the "run ingestion" caption.
- DoD #5 satisfied.

### Step 8 — Evaluation + verification (~1 day)
> **Found in Step 2:** `PROMPT` had no `{context}` placeholder since Phase 1 Step 5 — retrieved documents were **never sent to the LLM** (`context=` passed but silently dropped). The 13/13 Phase 1 eval was answered from model priors (tells: phantom citations `recipes/americanamberale.md`, hallucinated "Wyeast 3004"). Fixed by injecting context into the human turn; post-fix eval baseline: **11 pass / 0 fail / 2 manual**, Hefeweizen now correctly says Wyeast 3068. Step 8 works from this grounded baseline.

- Add ~8–10 rows to `tests/eval_questions.csv`: recipe lookup, recipe detail, BJCP stats, table-backed substitution, lab yeast questions (White Labs/Wyeast), hop variety questions, adversarial (no invented IBU).
- Full run via `tests/run_eval.py`: **13/13 Phase 1 regression gate** + new rows PASS/MANUAL.
- Re-run `tests/verify_model_swap.py`.
- Close Phase 1 grounding gaps where data allows (yeast strain "3004 vs 3068"; phantom citations for `maris-otter.md` / `chocolate-malt.md`).

---

## 8. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| **Source unreachable** (network down, repo removed, share offline) | Collectors fail soft with a warning; ingest skips missing `data_dirs`; fixtures keep chat + tests usable offline |
| **Web collector fragility** (whitelabs/wyeast restructure pages, block scrapers, or restrict ToS) | Isolate each in its own collector with a shared `web.py`; strict one-time polite fetching (tiny request count); if a site blocks automated access, fall back to manual seeding of strain `.md` files into the collector's dest namespace; verify ToS compliance when implementing Step 5. Hops are no longer scraped: `brewingtools_collector` is a single plain JSON download. beerMaverick is Cloudflare-blocked and is not used. |
| **Contract drift** between collector output and ingest expectations | Canonical-format contract (§3.1) + `tests/smoke_ingest.py` pins it; ingest only ever sees files |
| **Re-collect clobbering manual files** | Each collector owns only `collect_dest/<id>/` |
| **Licensing / personal data in git** | Nothing third-party or personal is committed; `collected_data/` gitignored; fixtures self-authored/minimal |
| **Retrieval dilution** (`top_k=6` vs growing store) | `top_k` raised by default (decision §9.4); type filter gives precise scoping; re-measure via eval |
| **BeerXML structure variance** | Renderer normalizes; fixture contract test over a known recipe |
| **New deps** (HTTP, HTML→md, optional PDF) | Prefer stdlib (`urllib`, `json`, `xml.etree`); pin anything added (`beautifulsoup4`/`html2text`, maybe `pypdf`) in `requirements.txt` |

---

## 9. Resolved Decisions (with user)

1. **`collect_dest`:** default `collected_data/` (gitignored) for easy development; users point it wherever they like. **`data_dirs`:** default `[data, collected_data]` — chat usable without running collectors.
2. **MVP collectors:** `diydog`, `bjcp`, `whitelabs`, `wyeast`, `brewingtools`.
3. **Self-authored CSV reference tables:** stay in repo `data/` (needed for testing, license-clean).
4. **Default `top_k`:** raised from 4 to **6**.
5. **`local_folder_collector`:** out of scope for Phase 2 — recorded in `BREW_BOT_PLAN.md` §11 for later consideration. (Basic own-file support exists via `data_dirs`, which accepts any folder of `.md`/`.xml`/`.csv`.)
