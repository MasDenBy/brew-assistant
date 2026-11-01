# Phase 2 — Data Expansion Plan

**Status:** Draft (branch `phase-2`, cut from `main` after PR #1 merged Phase 1)
**Parent plan:** `BREW_BOT_PLAN.md` (source of truth — §9 "Phase 2 — Data Expansion")
**Estimate:** ~1 week

---

## 1. Scope

Per `BREW_BOT_PLAN.md` §9, Phase 2 delivers:

1. **BeerXML recipe collection** — real recipes ingested as `recipe` chunks
2. **Hop/malt/yeast reference tables (CSV → text chunks)**
3. **BJCP style guidelines** — authoritative style data replacing/augmenting the generated Phase 1 style docs
4. **Improved chunking by entity type** — one recipe per chunk, one style per chunk, one hop per chunk (AGENTS.md §6.6)

**Also resolved in this phase:**
- `BREW_BOT_PLAN.md` §11 open question: *exact public data sources for BeerXML recipes and hop/malt references* (candidates in §3 below, to be confirmed with the user)
- Phase 1 eval grounding gaps (see §5, Step 7): hallucinated yeast strain "Wyeast 3004" (doc says 3068), phantom citations for files that don't exist (`ingredients/maris-otter.md`, `recipes/americanamberale.md`)

**Unchanged from Phase 1:** Streamlit UI, LangChain + ChromaDB + Ollama stack, model-swap-by-config rule, local-first constraint, eval CSV harness.

---

## 2. Definition of Done

1. `python ingest.py` ingests `.md` + `.xml` (BeerXML) + `.csv` (tables) and prints per-type counts including `recipe` and `table`; total chunk count grows from ~20 to 100+.
2. A recipe question (e.g. *"Show me an American IPA recipe"* or *"What hops are in a BrewDog Punk IPA clone?"*) returns a real recipe chunk with `[source: recipes/...]` citation.
3. A BJCP question (e.g. *"What is the IBU range for BJCP 21A American IPA?"*) answers from BJCP data with a citation.
4. A substitution question sourced from a CSV table (e.g. *"What can I substitute for Saaz?"*) returns table-backed options.
5. Sidebar gains a **search scope** filter (by entity type: style/hop/malt/yeast/recipe/table) that is applied to retrieval.
6. Eval regression: all 13 Phase 1 questions still PASS; ≥8 new Phase 2 questions added and passing.
7. Model swap still works with config-only change (no regression from Step 6).
8. Licensing-safe: no third-party raw files committed to the public repo without a license check (see §6).

---

## 3. Data Sources (candidates — confirm with user before downloading)

| Data | Candidate source | Notes / license |
|------|------------------|-----------------|
| **BeerXML recipes** | [stuartraetaylor/diydog-beerxml](https://github.com/stuartraetaylor/diydog-beerxml) — BrewDog DIY Dog recipes exported to BeerXML (~200 recipes) | DIY Dog book is free from BrewDog; verify repo license before committing files |
| **BeerXML recipes (alt)** | [art-defcon/beer](https://github.com/art-defcon/beer) personal recipe vault; [beerxml.com](https://beerxml.com) example files | License unclear — prefer DIY Dog set; examples are tiny but good as parser fixtures |
| **BeerXML parser** | stdlib `xml.etree.ElementTree` (preferred, zero new deps) or [hotzenklotz/pybeerxml](https://github.com/hotzenklotz/pybeerxml) | Renderer normalizes whatever the parser returns |
| **BJCP guidelines** | [bjcp.org downloads](https://www.bjcp.org/beer-styles/downloads-and-resources): 2015 single-file XML (Phil Murray, GitHub), separate-files XML (Matthew Smedberg), 2021 JSON versions (Andrew Scholer / Yuriy Krutilin, GitHub) | BJCP copyright: copying allowed for competitions/education; **keep raw guidelines out of the public repo** — commit only derived numeric facts or use a fetch script (decision in §6) |
| **Hop reference** | hopunion.com variety PDFs, hopswiki.com, yakimachief.com | Alpha acid %, flavor/aroma, typical uses |
| **Yeast reference** | whitelabs.com, wyeast.com public strain pages | Attenuation, flocculation, flavor — fixes the Phase 1 "Wyeast 3004 vs 3068" gap |
| **Malt reference** | briess.com, weyermann.de spec sheets | SRM/color, malt flavor roles; adds Maris Otter, crystal/caramel, chocolate malt docs |
| **Substitution tables** | No canonical CC source → **author CSVs ourselves** from brewing knowledge (consistent with Phase 1 generated docs) | e.g. `hop-substitutions.csv`: `ingredient,substitute,notes` |

---

## 4. Target Structure

```
brew-assistant/
├── PHASE_2_PLAN.md            # this file
├── data/
│   ├── styles/                # Phase 1 generated docs (kept)
│   ├── ingredients/           # Phase 1 docs + new malt/hop/yeast docs
│   ├── recipes/               # *.xml BeerXML files (committed only if license OK)
│   ├── tables/                # *.csv reference tables (one row = one entity)
│   └── bjcp/                  # BJCP styles — see licensing decision in §6
├── scripts/
│   └── fetch_data.py          # downloads raw sources (gitignored) — reproducibility
├── ingest.py                  # + XML/CSV loaders, per-entity chunking, type metadata
├── rag.py                     # + optional type filter on retrieve()
├── app.py                     # + sidebar "search scope" filter
└── tests/
    ├── eval_questions.csv     # + ~8-10 new Phase 2 rows
    ├── smoke_retrieval.py     # kept green (regression)
    └── smoke_rag.py           # kept green (regression)
```

---

## 5. Task Breakdown

### Step 1 — Data acquisition & licensing (~0.5 day)
- Confirm §3 sources with the user; decide BJCP commit-vs-fetch strategy (§6 decision).
- Write `scripts/fetch_data.py`: downloads the chosen BeerXML set + BJCP files into `data/` (gitignored raw dirs where needed).
- Author CSV tables in `data/tables/`: `hop-substitutions.csv`, `hop-alpha-acids.csv`, `yeast-strains.csv`, `malt-types.csv` (columns include a `type` hint or use the filename-prefix convention from Phase 1's `hop-*.md`).
- Update `.gitignore` for any raw/download dirs.

### Step 2 — BeerXML ingestion (~1 day)
- Parser: `xml.etree.ElementTree` (stdlib) → recipe objects.
- Renderer: one readable text block per recipe — name, style, batch size, OG/FG/ABV/IBU, fermentables (name + amount), hop schedule (name/amount/time/use), yeast, short notes. Cap length so one recipe = one chunk (≤ ~3000 chars; truncate mash/notes first).
- Metadata: `source`, `type=recipe`, `name`, `style`, plus a few searchable fields (`og`, `ibu` if present).
- `ingest.py` globs `data/recipes/**/*.xml` alongside `*.md`/`*.txt`.

### Step 3 — CSV table ingestion (~0.5 day)
- Loader: `data/tables/*.csv` → one chunk per row entity, rendered as text with a one-line header describing the columns (so the model can read it standalone).
- Chunk metadata: `source` (filename), `type` from a `type` column or filename prefix (AGENTS.md §6.6: hop/malt/yeast), `name` (entity name column).

### Step 4 — BJCP ingestion (~1 day)
- Convert each BJCP style → one chunk: code + name, overall impression (short excerpt), vital statistics ranges (OG/FG/ABV/IBU/SRM), characteristic ingredients.
- Strategy per licensing decision: parse at ingest time from gitignored raw files (skips gracefully when absent), or commit derived per-style `.md` containing only numeric facts + brief excerpts.
- Add a handful of new `data/ingredients/` docs sourced from lab/malt specs (Maris Otter, crystal/caramel malt, chocolate malt, White Labs/Wyeast strain rows) to close the Phase 1 phantom-citation and hallucinated-strain gaps.

### Step 5 — Chunking by entity type (~0.5 day)
- Replace the single 2000-char splitter with per-type strategy:
  - `recipe`, `bjcp_style`, table rows → **no split** (entity = chunk)
  - `.md` docs → existing `RecursiveCharacterTextSplitter`, split only if > ~3000 chars
- Extend the ingest summary to print counts per type (`style/hop/malt/yeast/recipe/table`).

### Step 6 — Retrieval filter + UI (~0.5 day)
- `rag.retrieve(query, k, types=None)` → Chroma filter `{"type": {"$in": types}}`; thread through `get_retrieved_sources`.
- `app.py` sidebar: **Search scope** multiselect (all types by default) passed into retrieval; sources panel already shows `[type]` labels.

### Step 7 — Evaluation + verification (~1 day)
- Add ~8–10 rows to `tests/eval_questions.csv`, e.g.:
  - Recipe: *"Show me a recipe for an American IPA"* → expects recipe citation
  - Recipe detail: *"What hops are in <recipe name>?"* → expects hop names from that recipe
  - BJCP: *"What is the IBU range for BJCP 21A American IPA?"* → expects IBU numbers
  - Table: *"What can I substitute for Saaz hops?"* → expects table-backed alternatives
  - Yeast/malt comparison rows using the new reference docs
  - Adversarial: *"Calculate the IBU of a recipe with 20g Cascade at 60 min"* → still refuses to invent numbers
- Run the full CSV (`tests/run_eval.py`): **all 13 Phase 1 questions must remain PASS** (regression gate); new rows pass or are marked MANUAL for review.
- Re-verify model swap (`tests/verify_model_swap.py`) after config/UI changes.

---

## 6. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| **Licensing** (public repo `MasDenBy/brew-assistant`): BJCP guidelines and third-party recipe sets have restrictive terms | **Decision needed:** (a) commit only derived numeric facts/short excerpts, or (b) keep raw files gitignored + `scripts/fetch_data.py` (recommended for BJCP). BeerXML sets: check repo license before committing; DIY Dog set is the safest candidate |
| Chunk inflation: 100+ chunks with `top_k=4` may dilute retrieval | Re-measure with eval after Step 5; consider raising default `top_k` (config change) or adding the type filter as a default scope |
| BeerXML files vary in structure/quality | Renderer normalizes output; add a fixture smoke test (`tests/smoke_ingest.py`) over one known recipe |
| Oversized recipes blow the chunk budget | Renderer caps detail (full hop schedule + fermentables; truncate mash/notes); guard in Step 5 |
| New dependency churn (pybeerxml, requests for fetch) | Prefer stdlib `ElementTree`; pin anything added in `requirements.txt` |
| Regression of Phase 1 behavior (streaming, citations, model swap) | Keep `smoke_retrieval.py` / `smoke_rag.py` green; full 13-question eval is the gate |

---

## 7. Out of Scope (deferred)

- **Phase 3:** citation validation (phantom-citation detection noted in eval rows 9/10), formal pass/fail tracking UI, documentation on adding models/data
- **Phase 4:** recipe calculator (IBU/ABV/SRM as a tool call), batch scaling, off-flavor troubleshooting, fine-tuning

---

## 8. Decisions Needed from User (before Step 1)

1. **BeerXML recipe set:** DIY Dog collection (~200 recipes) as primary? Any preference for additional sets?
2. **BJCP handling:** commit derived per-style facts (small, license-gray) vs. gitignored raw + fetch script (recommended)?
3. **BJCP version:** 2021 (current) via the community JSON/XML exports — OK?
4. **New default `top_k`:** keep 4 or raise (e.g. 6) once chunk count grows?
