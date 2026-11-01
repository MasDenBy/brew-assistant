# Phase 2 — Data Collection & Expansion Plan

**Status:** Draft v2 (replanned around the data-collector architecture; branch `phase-2`)
**Parent plan:** `BREW_BOT_PLAN.md` (source of truth — §9 "Phase 2 — Data Expansion")
**Estimate:** ~5–6 days

---

## 1. Design Principles (decided with user)

1. **Repo `data/` = fixtures only.** Only the small files needed to run smoke tests and the eval regression — nothing bulk, nothing third-party, nothing personal.
2. **Bulk/personal data lives outside git**, in a user-controlled directory (e.g. `\\myshare\brew`). It can be big, licensed, or private.
3. **A separate collector application** fetches documents from each source and writes them *in canonical RAG format* to that directory. One **data collector = one class = one source** (e.g. `bjcp_collector.py`, `whitelabs_collector.py`).
4. **Extensible by design:** adding a new source means adding one collector class and enabling it in config — `ingest.py`, `rag.py`, `app.py` never change for a new source.
5. **User's own data fits the same path:** own recipes/ingredients are dropped into (or copied into) the same directory — no special cases.

### Target flow

```
1. python -m collectors          # run enabled collectors → files land in collect_dest (e.g. \\myshare\brew)
2. python ingest.py              # reads config data_dirs (fixtures + collect_dest) → ChromaDB
3. streamlit run app.py          # chat
```

---

## 2. Scope

1. **Collector application** (new component): framework + runner + first collectors
2. **Ingestion reads configurable data dirs** (`data_dirs` in `config.yaml`) instead of a hardcoded `data/`
3. **New formats via canonical output:** BeerXML recipes, CSV reference tables, BJCP styles — collectors normalize everything to formats ingest already understands (`.md` / `.txt` / `.xml` / `.csv`)
4. **Chunking by entity type** (one recipe/style/hop per chunk — AGENTS.md §6.6)
5. **Search-scope filter** by entity type (retrieval + sidebar)
6. **Evaluation:** regression gate on the 13 Phase 1 questions + new Phase 2 questions

**Resolved from v1 of this plan:** the BJCP/BeerXML *licensing decision* mostly evaporates — raw third-party files are never committed; collectors download them to the user's local directory. Repo fixtures are self-authored or minimal samples.

---

## 3. Architecture

### 3.1 Collector framework

```
collectors/
├── __init__.py          # registry: discovers/registers collectors
├── __main__.py          # runner: python -m collectors [--only bjcp,diydog]
├── base.py              # DataCollector ABC
├── bjcp_collector.py    # BJCP guidelines → canonical style files
├── diydog_collector.py  # BrewDog DIY Dog BeerXML recipes → recipes/*.xml
├── local_folder_collector.py  # user's own files → copy/normalize into dest
└── (whitelabs_collector.py, ... # Phase 2+ / user-contributed)
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

- **Registration:** decorator `@register` in `collectors/__init__.py`; runner iterates the registry, filtered by the `collectors:` list in `config.yaml`.
- **Namespace rule:** each collector owns `collect_dest/<collector_id>/` — re-running one collector never touches another's files or the user's manually placed files.
- **Output contract (collector ↔ ingest):** canonical formats only —
  `recipes/*.xml` (BeerXML), `bjcp/*.md` or `*.xml`, `tables/*.csv`, `ingredients/*.md`, `styles/*.md`.
  Type inference extends Phase 1's convention: type from the *type-directory name* at any depth (`recipes/`, `bjcp/`, `tables/`, `styles/`) or the filename prefix (`hop-*`, `malt-*`, `yeast-*`).

### 3.2 Config additions (`config.yaml`)

```yaml
collect_dest: "\\\\myshare\\brew"      # where collectors write (outside git)
data_dirs:                              # where ingest reads from
  - data                                # repo fixtures (default — tests work offline)
  # - \\\\myshare\\brew                  # user adds their collected/personal library
collectors: [diydog, bjcp, local_folder]  # enabled collectors for `python -m collectors`
```

`data/` fixtures and the user's share can both be active: ingest globs every dir in `data_dirs`.

### 3.3 Ingestion changes

- `load_documents()` iterates `cfg["data_dirs"]` (skip missing dirs with a warning — the share may be offline).
- `infer_type()` extended for type-directories (`recipes` → `recipe`, `bjcp` → `bjcp_style`, `tables` → from CSV `type` column or filename).
- No knowledge of collectors — ingest only reads files. The collector app and ingestion are decoupled through the **file-format contract**.

---

## 4. Definition of Done

1. `python -m collectors` runs the enabled collectors, writes canonical files under `collect_dest/<id>/`, prints a per-collector summary, and skips gracefully (warning, non-zero report) when a source is unreachable.
2. Repo `data/` contains **only fixtures**: the existing 20 small docs + a tiny sample BeerXML + a small sample CSV — enough for smoke tests and eval; no bulk/third-party/personal files in git.
3. `python ingest.py` works with default config (fixtures only, zero network) and additionally ingests the share when `data_dirs` is configured; prints per-type counts (`style/hop/malt/yeast/recipe/bjcp_style/table`).
4. Recipe question → real recipe chunk with citation; BJCP question → guideline numbers with citation; table substitution question → table-backed answer.
5. Sidebar **Search scope** filter (entity types) applied to retrieval.
6. Eval: all 13 Phase 1 questions still PASS; ≥8 new Phase 2 questions added and passing.
7. Model swap still config-only (regression).
8. **Extension documented:** "add a source" = subclass `DataCollector` + `@register` + add id to config — no changes to ingest/rag/app (documented in `collectors/base.py` docstring or a short `collectors/README.md`).

---

## 5. Collector Catalog (MVP set + future)

| Collector | Source | Output under `collect_dest/<id>/` | Phase |
|-----------|--------|-----------------------------------|-------|
| `diydog_collector` | [stuartraetaylor/diydog-beerxml](https://github.com/stuartraetaylor/diydog-beerxml) (~200 recipes) | `recipes/*.xml` | 2 MVP |
| `bjcp_collector` | [bjcp.org downloads](https://www.bjcp.org/beer-styles/downloads-and-resources) — 2021 JSON/XML community exports | `bjcp/*.md` (one style per file) | 2 MVP |
| `local_folder_collector` | User's own folder of recipes/ingredients (BeerXML, md, txt) | `recipes/`, `ingredients/`, `styles/` as applicable | 2 MVP |
| `whitelabs_collector` / `wyeast_collector` | Lab strain pages | `ingredients/yeast-*.md` | later |
| `hop_union_collector` / `malt_collector` | Spec-sheet PDFs (hopunion, briess, weyermann) | `ingredients/hop-*.md`, `malt-*.md` | later |

**Repo fixtures (in `data/`, committed):** the 20 Phase 1 docs (they double as eval grounding), 1–2 hand-written sample `.xml` recipes, 1–2 small `.csv` tables. Self-authored CSV reference tables stay in the repo for now (small, license-clean, needed by tests) — see decision §9.3.

---

## 6. Target Structure

```
brew-assistant/
├── PHASE_2_PLAN.md
├── config.yaml                # + collect_dest, data_dirs, collectors
├── collect.py                 # thin alias → python -m collectors (optional)
├── collectors/                # NEW — the collector application (see §3.1)
│   ├── base.py
│   ├── __main__.py
│   ├── bjcp_collector.py
│   ├── diydog_collector.py
│   └── local_folder_collector.py
├── data/                      # FIXTURES ONLY (git stays small & clean)
│   ├── styles/  ingredients/  # Phase 1 docs (kept)
│   ├── recipes/               # 1-2 hand-written sample .xml
│   └── tables/                # small self-authored .csv
├── ingest.py                  # + data_dirs iteration, extended type inference
├── rag.py                     # + optional type filter
├── app.py                     # + sidebar search scope
└── tests/
    ├── eval_questions.csv     # + ~8-10 rows
    ├── smoke_ingest.py        # NEW: contract test — ingest fixtures end-to-end, offline
    ├── smoke_retrieval.py     # kept green
    └── smoke_rag.py           # kept green
```

---

## 7. Task Breakdown

### Step 1 — Collector framework (~0.5 day)
- `collectors/base.py`: `DataCollector` ABC (`id`, `description`, `collect(dest)`), `@register` registry.
- `collectors/__main__.py`: reads config, runs enabled collectors in order, per-collector success/skip/fail summary, `--only` flag for running one collector.
- Config keys: `collect_dest`, `data_dirs`, `collectors`.
- Document the output contract + "how to add a collector" (§3.1, DoD #8).

### Step 2 — Ingestion: multi-dir + contract test (~0.5 day)
- `data_dirs` iteration with missing-dir warning; extended `infer_type()` (`recipes/`, `bjcp/`, `tables/` + CSV `type` column).
- **Fixtures:** add 1–2 sample BeerXML recipes + 1–2 small CSVs under `data/`.
- New `tests/smoke_ingest.py`: runs ingestion over fixtures only (offline), asserts per-type counts and one chunk per recipe — this pins the collector↔ingest contract.

### Step 3 — First collectors (~1.5 days)
- `diydog_collector`: download repo archive → extract `*.xml` → `recipes/`; license check recorded in the collector docstring.
- `local_folder_collector`: configured source folder → copy/normalize user's `.xml`/`.md`/`.txt` into type dirs (read-only w.r.t. the source).
- Both idempotent (clear own subtree) and fail-soft (network/folder missing → warn + skip).

### Step 4 — BJCP collector (~1 day)
- Download the 2021 community JSON/XML export → render one `.md` per style (code, name, vital-stat ranges, short impression excerpt) → `bjcp/`.
- Raw downloads stay in the collector's temp/dest area — never committed.

### Step 5 — Chunking by entity type (~0.5 day)
- Per-type strategy: `recipe`, `bjcp_style`, table rows → no split (entity = chunk, with a length guard); `.md` → existing splitter only when oversized.
- Renderer caps recipe detail so one recipe fits one chunk (full hop schedule + fermentables; truncate mash/notes first).
- Extend the ingest summary to per-type counts.

### Step 6 — Retrieval filter + UI (~0.5 day)
- `rag.retrieve(query, k, types=None)` → Chroma filter `{"type": {"$in": types}}`; thread through `get_retrieved_sources`.
- `app.py` sidebar: **Search scope** multiselect (default: all types).

### Step 7 — Evaluation + verification (~1 day)
- Add ~8–10 rows to `tests/eval_questions.csv`: recipe lookup, recipe detail, BJCP stats, table-backed substitution, yeast/malt comparisons, adversarial (no invented IBU).
- Full run via `tests/run_eval.py`: **13/13 Phase 1 regression gate** + new rows PASS/MANUAL.
- Re-run `tests/verify_model_swap.py`.
- Close Phase 1 grounding gaps where data allows (yeast strain "3004 vs 3068"; phantom citations for `maris-otter.md` / `chocolate-malt.md` — via fixtures or a later lab collector).

---

## 8. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| **Source unreachable** (network down, share offline, repo removed) | Collectors fail soft with a warning; ingest skips missing `data_dirs` with a warning; fixtures keep tests/chat usable offline |
| **Contract drift** between collector output and ingest expectations | Canonical-format contract (§3.1) + `tests/smoke_ingest.py` pins it; ingest only ever sees files |
| **Re-collect clobbering user's manual files** | Each collector owns only `collect_dest/<id>/`; user's own files live outside collector subtrees (or under `local_folder`'s namespace) |
| **Licensing / personal data in git** | Nothing third-party or personal is committed; collectors write outside the repo; fixtures are self-authored/minimal |
| **Chunk inflation dilutes retrieval** (`top_k=4` vs 100+ chunks) | Re-measure with eval after Step 5; raise default `top_k` via config if needed (decision §9.4) |
| **BeerXML structure variance** | Renderer normalizes; fixture contract test over a known recipe |
| **New deps** (HTTP for collectors, JSON/BeerXML parsing) | Prefer stdlib (`urllib`, `json`, `xml.etree`); pin anything added in `requirements.txt` |

---

## 9. Decisions Needed from User

1. **`collect_dest` default:** `\\myshare\brew` as in your example? And should `data_dirs` default to `[data]` only (user opts in to the share), or `[data, collect_dest]` automatically?
2. **MVP collector set:** is `diydog + bjcp + local_folder` the right first three? (Whitelabs/Wyeast/hop/malt collectors later.)
3. **Self-authored CSV tables:** keep the small reference tables in repo `data/` (needed by tests, license-clean) — or route them through a `static_tables_collector` so *everything* lives outside git?
4. **New default `top_k`:** keep 4 or raise (e.g. 6) once the collection grows?
5. **Local-folder semantics:** should `local_folder_collector` *copy* files into the dest (self-contained) or *read them in place* (no duplication)?
