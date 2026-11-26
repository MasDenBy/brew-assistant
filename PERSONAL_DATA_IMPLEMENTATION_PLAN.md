# Personal Brewer Data — Implementation Plan (Changes)

**Status:** Ready for review — no code written
**Date:** Thu Nov 26 2026
**Design / decisions:** `PERSONAL_DATA_PLAN.md` (authoritative for scope and architecture)
**Parent:** `BREW_BOT_PLAN.md`

This document does **not** restate the design. It maps that design onto the
repository as it exists today: what must be added, what must be edited, in
which order, and how each step is verified.

**Scope revision (2026-11-26):** batches and equipment are out of scope. The
design doc was updated in the same pass; see §3 for the resolved gaps.
**Intake revision (2026-11-26):** pasted plain text is the only MVP input; the
extractor reads narrative process steps and returns a deterministic plan.
BeerXML upload is deferred.
**Optionality (2026-11-26):** Brewfather is an enhancement, not a dependency.
Public chat and text intake/local drafts work with it disabled.

---

## 1. Verified current state (2026-11-26)

| Fact | Evidence |
|---|---|
| No `personal/`, `integrations/`, or `agent/` package exists | `git ls-files` |
| No `.env`, no `user_data/` directory | filesystem check |
| `config.py` resolves only `data_dir`, `chroma_dir`, `collect_dest` | `config.py:23` |
| `python-dotenv`, `requests`, `pandas` already pinned | `requirements.txt` |
| No `pytest`; every test is a standalone script | `.venv\Scripts\python.exe tests\test_*.py` |
| `rag.py` prompt has no personal-data placeholder | `rag.py:234` (`PROMPT`) |
| History budget ignores any future personal block | `rag.py:654` (`trim_history`) |
| `app.py` is one chat page; no tabs, no personal panel | `app.py:84` |
| Re-ingest already uses the subprocess pattern to avoid stale handles | `app.py:140` |
| Ollama is running and **all configured models advertise `tools`** | `/api/show` probe |
| `ChatOllama` 1.1.0 exposes `bind_tools` | introspection |

Capability probe run today:

```
qwen2.5:7b     -> completion, tools
gemma4:12b     -> completion, vision, audio, tools, thinking
qwen3.5:9b     -> completion, vision, tools, thinking
mistral:7b     -> completion, tools
qwen2.5:1.5b   -> completion, tools
```

This closes the Phase 0 question "confirm `/api/show` capabilities for the
configured chat models". The detection code still has to be written and tested,
but the API shape is confirmed.

---

## 2. Gap summary (what "changes" means)

| Area | Today | Required change |
|---|---|---|
| Config | YAML only, three path keys | path/env/tool keys added **when their packet needs them** (§3.5) |
| Storage | none | SQLite schema + repository under `personal/` |
| Brewfather | none | `integrations/` client (allowlisted), mapper, sync provider, CLI |
| Chat context | public Chroma only | deterministic prefetch + optional personal block, budgeted |
| Prompt | public-context rules only | personal-data rules appended **only when personal data exists** |
| Tools | none | capability detection + 3 bounded read-only tools + loop |
| UI | single chat page | sidebar integration section, "Your data" panel, freshness, tabs, Import tab |
| Intake | BeerXML → markdown chunk only | text-first prose extractor (header, ingredients, mash/boil/ferment/package), deterministic process plan, review form |
| Write-back | none | preview + confirm + one `POST`, audit row |
| Eval | keyword CSV, no integration awareness | opt-in `tests/eval_personal.csv` + `tests/eval_intake.csv` |
| Docs | plan docs only | README, AGENTS.md, BREW_BOT_PLAN pointer |
| Repo hygiene | no personal paths ignored | `user_data/`, `.env` in `.gitignore` |

---

## 3. Decisions applied (2026-11-26)

These replace the earlier "design corrections". Items 1–2 are scope removals;
items 3–6 answer the four open gaps; items 7–8 are small correctness fixes from
the review; item 9 records that Brewfather is optional.

### 3.1 Batches removed

No batch sync, no `batches` table, no prefetch intent, no tool, no batch
context. `GET /v2/batches` is never called. "What is fermenting?" is not an
answerable question in this app.

### 3.2 Equipment removed

No `equipment` table, no `brewer_preferences` table, no derived profiles, no
local default, no equipment panel, no equipment field in the draft form, and no
`equipment` object in the create payload. The nested `equipment` inside a
synced recipe stays in `raw_json` and is otherwise ignored.

### 3.3 Gap 4 — what `extract.py` is for, and the solution

**Why it exists:** `personal/extract.py` turns the **pasted magazine/web
recipe** — the MVP's only intake — into a validated draft plus a proposed
brewing plan. `ingest.render_recipe` (`ingest.py:194`) is not reusable for this:
it emits *markdown prose* for the RAG chunk store, not a structured object with
amounts, units, timings, and process steps. Stock checks and the create payload
need the structured form.

The input is free-form prose, often with no reliable headings:

- header stats (`5 gallons/19 L`, `OG = 1.071`, `IBU = 21`);
- ingredient lines with mixed units (`5.4 lbs. (2.4 kg)`, `13 oz. (369 g)`);
- hop AAU plus alpha and grams;
- alternative yeasts (`Wyeast 1762, White Labs WLP540, or SafAle T-58`);
- narrative steps (roast figs, infusion mash at 151 °F/66 °C for 90 min,
  sparge to 6.5 gal, 90 min boil, knockout steep, ferment at 66 °F, prime/keg).

**Solution:** one LLM extraction call with a strict, process-aware schema, then
a **deterministic plan renderer**:

- `extract_recipe(text) -> dict` (`personal/extract.py`): header + ingredient
  tables + `mash` / `boil` / `fermentation` / `packaging` blocks +
  `process_notes` + `missing_fields` + `suggestions`. One repair retry.
- `render_plan(spec) -> str` (`personal/plan.py`): ordered plan
  (prepare → mash → sparge → boil → knockout/chill → ferment → package) built
  only from extracted values; gaps read "not stated". The LLM never writes the
  plan, so it cannot invent a temperature or time.

No BeerXML parser in MVP. `ingest.py` is **not touched**.

### 3.4 Gap 5 — what the eval rows are for, and the solution

**Why it exists:** the integration needs its own regression rows (inventory
listing, coverage, "what can I brew", create-confirmation), and text intake
needs rows that run without a connection. Brewfather rows cannot run in the
normal eval when `enabled: false`, or the normal eval run goes red for a feature
that is intentionally off. The existing CSV has fixed columns and the runner
writes a fixed `fieldnames` list (`tests/run_eval.py:206`), so a naive
`requires` column would be dropped.

**Solution:** keep the existing eval untouched and add two opt-in files with the
same columns:

- `tests/eval_personal.csv` + `--personal` — Brewfather-dependent rows; refuses
  to run when the integration is disabled or `user_data/brew.db` is empty.
- `tests/eval_intake.csv` + `--intake` — text-intake rows; runs with or without
  Brewfather (stock/create simply unavailable).

```powershell
.venv\Scripts\python.exe tests\run_eval.py --personal
.venv\Scripts\python.exe tests\run_eval.py --intake
```

Neither flag touches `tests/eval_questions.csv`. No `SKIP` status, no
column-schema change, no change to the existing writer.

### 3.5 Gap 6 — config keys are added when needed

No pre-work. Each key lands in the packet that first uses it:

| Key / behaviour | Packet |
|---|---|
| `user_db` path resolution | W1 (DB) |
| `.env` loading + `BREWFATHER_USER_ID` / `BREWFATHER_API_KEY` | W2 (client) |
| `integrations.brewfather.*` block | W2 |
| `agent.tools` / `max_tool_steps` | W4 (tools) |
| `models.<name>.supports_tools` override | W4 (tools) |
| `agent.personal_context_max_chars` / `personal_top_k_cut` | W3 (prefetch) |
| `intake.model` / `extraction_temperature` / `max_source_chars` | W5 (intake) |
| `intake.unit_system` | W5 (intake) |

Until then the app runs exactly as today with no new config required.

### 3.6 Gap 7 — what "clear personal data" is for, and the solution

**Why it exists:** two reasons, neither involving `.env` or credentials.

1. **ToS deletion duty.** The Brewfather API terms require the local copy to be
   deleted when the user disconnects. The app must have a button that actually
   drops the synced data.
2. **User control.** A one-click way to remove personal brewing data is expected
   in a local-first app.

**Solution:** a **Clear personal data** action drops the synced tables (recipes,
derived recipe ingredients, inventory, sync state/runs), learned aliases, and
local drafts including their source text. The hash-only write audit is kept
unless the user opts to clear it. Credentials are user-owned environment
variables and are untouched; there is no "erase `.env`" button, because the app
does not own that file and deleting it adds no value.

### 3.7 Sync resources and soft delete

- Logical resources: `recipes`, `inventory.hops`, `inventory.fermentables`,
  `inventory.yeasts`, `inventory.miscs`. `sync(["inventory"])` expands to the
  four kinds; TTL and error state stay per kind.
- A **successful full pass** marks rows absent from the response as
  `deleted_at`. A failed pass touches nothing.

### 3.8 Create payload without equipment

Since drafts never carry equipment, the `POST /v2/recipes` payload omits it.
Confirm during W6, with a disposable recipe, that Brewfather accepts a recipe
without `equipment`. If it does not, the create stops with a clear message
("Brewfather requires an equipment profile; add one in Brewfather first") and
sends nothing. Do not invent a profile or copy one silently.

### 3.9 Brewfather is optional

Public chat, retrieval, citations, model selection, text intake, the process
plan, and local drafts must work with `integrations.brewfather.enabled: false`
and no `.env`. The integration adds inventory-aware stock checks and create; it
is not a dependency.

- Chat: no personal inventory/recipe context, no personal prompt block, no tools,
  no freshness caption — exactly today's path for ordinary questions. Recipe
  paste detection still runs.
- Import tab: extraction, plan, and local drafts work; stock shows "Brewfather
  not connected / no inventory synced"; create is disabled with a clear message.
- Tests: add a no-`.env` startup/import smoke check; existing chat/eval remain
  the hard gate.

---

### 4.1 New files

| File | Purpose | Key surface |
|---|---|---|
| `personal/__init__.py` | package | — |
| `personal/db.py` | connection, schema, migrations, clear-data | `connect()`, `init_db()`, `clear_personal_data()` |
| `personal/repository.py` | typed DB access | upserts, queries, drafts, audit |
| `personal/matching.py` | ingredient identity | `normalize_name()`, `match_item()` |
| `personal/stock.py` | deterministic coverage | `check_recipe_stock()`, `rank_brewable()` |
| `personal/prefetch.py` | intent classification + context builder | `classify()`, `build_context()` |
| `personal/render.py` | compact + injection-safe text | `wrap_user_data()`, truncation |
| `personal/extract.py` | text → draft spec + suggestions | `extract_recipe()` |
| `personal/plan.py` | deterministic process plan | `render_plan()` |
| `integrations/__init__.py` | package | — |
| `integrations/base.py` | provider contract | `BrewingDataProvider`, `SyncReport` |
| `integrations/brewfather/__init__.py` | package | — |
| `integrations/brewfather/client.py` | HTTP chokepoint | allowlist, paging, retries, redaction |
| `integrations/brewfather/mapper.py` | API JSON → rows | recipes, inventory items |
| `integrations/brewfather/provider.py` | sync orchestration | `sync()`, `create_recipe()` |
| `integrations/brewfather/sync_cli.py` | subprocess entry point | `python -m integrations.brewfather.sync_cli` |
| `agent/__init__.py` | package | — |
| `agent/capabilities.py` | `/api/show` detection | `supports_tools()` |
| `agent/tools.py` | schemas + handlers | `TOOL_SCHEMAS`, `execute()` |
| `agent/loop.py` | bounded tool loop | `answer()` |
| `ui/__init__.py` | UI modules (recommended) | — |
| `ui/sidebar.py` | sidebar sections | — |
| `ui/chat.py` | chat tab | — |
| `ui/import_tab.py` | import/review/create | — |
| `tests/fixtures/brewfather/*.json` | synthetic API payloads | — |
| `tests/fixtures/recipes/*.txt` | synthetic magazine-style prose recipes | — |
| `tests/eval_personal.csv` | opt-in Brewfather eval rows | — |
| `tests/eval_intake.csv` | opt-in text-intake eval rows (no Brewfather) | — |
| `tests/test_*.py` | offline scripts (§6) | — |

`ui/` extraction is a recommendation, not a requirement. `app.py` is 240 lines
today; the planned additions roughly double it. Splitting now keeps the chat
path reviewable; if you prefer one file, keep `app.py` and add the Import tab
inline.

### 4.2 Existing files to edit

| File | Change | When |
|---|---|---|
| `.gitignore` | add `user_data/`, `.env` | W1 |
| `config.py` | resolve `user_db`; later `load_dotenv()` and tool-support helper | W1/W2/W4 |
| `config.yaml` | add `integrations:`, `agent:`, `user_db:` blocks as used | W1–W4 |
| `rag.py` | optional `personal_text` in `trim_history`, `_build_messages`, `history_turn_count`, `stream_answer`, `ask`; `PERSONAL_DATA_RULES` appended only when text is present | W3 |
| `app.py` | tabs, chat paste detection + "Review draft", sidebar sync/clear-data, capability warning, prefetch, top-k cut, personal panel, status line | W3/W4/W5 |
| `tests/run_eval.py` | `--personal` and `--intake` flags reading the opt-in CSVs; existing path untouched | W7 |
| `README.md` | setup, scopes, sync, import, tests, privacy | W7 |
| `AGENTS.md` | new §6.5 personal data (state, invariants, test commands) | W7 |
| `BREW_BOT_PLAN.md` | §9 pointer to this plan | W7 |

### 4.3 Explicitly unchanged

- `ingest.py` chunking, `data/`, `collected_data/`, Chroma collections.
- `rag.py` public prompt text, `format_context`, `normalize_citations`,
  `is_refusal`, disambiguation, style/part pins.
- The `[N]` citation scheme and the public sources panel.
- Memory/`num_ctx` policy (`get_llm` precedence stays as tested).
- `tests/eval_questions.csv` and its writer.

The hard regression gate: with `integrations.brewfather.enabled: false`, the
message list produced by `_build_messages` must be **identical** to today's
(covered by `test_personal_prompt.py`).

---

## 5. Work packets (ordered)

Estimates exclude the live-API spike. Dependencies are explicit.

### W0 — Spike + fixtures (0.5 day, partially done)

- **Done today:** `/api/show` shape and tool capabilities for all five models;
  `bind_tools` availability; plan/code gap review.
- **Do now, offline:** create `tests/fixtures/brewfather/` shaped exactly like
  the documented objects:
  - `recipes_page1.json` — two recipes with fermentables/hops/yeasts;
  - `inventory_hops.json` — including an item with `inventory: null`;
  - `inventory_fermentables.json`, `inventory_yeasts.json`, `inventory_miscs.json`;
  - `errors/401.json`, `errors/403.json`, `errors/429.json`.
  - `tests/fixtures/recipes/*.txt` — two synthetic magazine-style recipes
    (not the copyrighted example): prose steps, mixed units, alternative
    yeasts, missing name/style.
- **Do with a real key (read-only):** confirm field names (`updatedAt` vs
  `_timestamp`), paging shape, and the account's actual scopes. Record results
  in `PERSONAL_DATA_PLAN.md` §4.
- **Exit:** fixtures committed; spike notes appended; no app code depends on
  live calls.

### W1 — Local store (0.5–1 day)

- **Files:** `personal/db.py`, `personal/repository.py`,
  `tests/test_personal_db.py`; edits to `config.py`, `config.yaml`,
  `.gitignore`.
- **Steps:**
  1. Add `user_data/brew.db` resolution and `user_data/`/`.env` ignores.
  2. Implement the schema: `meta`, `sync_runs`, `sync_state`, `recipes`,
     `recipe_fermentables`, `recipe_hops`, `recipe_yeasts`, `recipe_miscs`,
     `inventory_items`, `ingredient_aliases`, `recipe_drafts`, `write_audit`.
     No `batches`, no `equipment`, no `brewer_preferences`.
  3. `PRAGMA journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout`.
  4. One connection per operation (context manager). No module-level handle.
  5. Repository: upsert recipe (+derived ingredient rows rebuilt in the same
     transaction), inventory item; queries by name/kind; drafts; `write_audit`.
  6. `clear_personal_data(include_drafts=True, include_audit=False)` drops
     the synced tables, aliases, and (by default) drafts/source text; the audit
     log is opt-in to clear.
- **Tests:** create from empty; idempotent re-init; upsert with soft delete;
  replaced derived rows don't duplicate; failed-sync preservation is a
  repository guarantee (no delete call); clear-data drops synced tables,
  aliases, and drafts by default, keeps audit unless requested; WAL mode
  reported.
- **Exit:** all offline; no network, no LLM, no Streamlit.

### W2 — Brewfather client + mapper + sync (1–1.5 days)

- **Files:** `integrations/base.py`, `integrations/brewfather/{client,mapper,
  provider,sync_cli}.py`, tests; edits to `config.py` (`.env`, integration
  block) and `config.yaml`.
- **Client:**
  - `requests.Session`, Basic auth from env, 20 s timeout, descriptive UA,
    `min_request_interval_s`.
  - `_request(method, path, …)` is the only socket path. Reject any
    `(method, path)` outside `{("GET", …), ("POST", "/recipes")}` **before** the
    session call; additionally reject the POST unless `allow_writes`.
  - `get_all(resource, kind=None, complete=True)` with `start_after`, stop on a
    short page, hard request cap.
  - Retry 3× on 429/5xx; never on 401/403. Precise error messages per §7.
  - Redact `Authorization`; never log full payloads.
- **Mapper:** recipe → row + normalized ingredient rows; inventory item
  (`inventory: null` stays `None`, never 0); unit normalization; missing fields
  stay null. The nested `equipment` stays inside `raw_json`, unused.
- **Provider:** TTL skip, per-resource transaction, soft-delete unseen rows only
  after a fully successful pass, resource expansion for the four inventory
  kinds, `SyncReport`.
- **CLI:** `python -m integrations.brewfather.sync_cli [--force] [--resources …]`
  prints one JSON `SyncReport` line for the UI subprocess to parse.
- **Tests:** paging/short page; 429 + `Retry-After`; 401/403 non-retry; request
  cap; allowlist rejects PATCH/PUT/DELETE and POST when writes are off; mapper
  units; failed sync preserves rows; redaction.
- **Exit:** a full sync against a fake transport populates the DB; no real
  network in tests.

### W3 — Personal context in chat (1 day)

- **Files:** `personal/prefetch.py`, `personal/render.py`,
  `tests/test_personal_prefetch.py`, `tests/test_personal_prompt.py`; edits to
  `rag.py`, `app.py`, `ui/*`.
- **Prefetch:** keyword sets + last 3 user turns (`rag.expand_query` reuse) →
  `inventory | recipe_lookup | stock_check | create`. Builders read SQLite,
  render compact text (amounts, alpha, freshness), cap at
  `personal_context_max_chars`, return `PersonalContext(text, records, used)`.
  Recipe-paste detection is added in W5, when the extractor exists. With the
  integration disabled or no DB, inventory/recipe/stock prefetch returns an
  empty context and the prompt is exactly today's; recipe-paste detection is
  not gated.
- **Rendering:** every personal value goes through `wrap_user_data()`:
  `<user_data>…</user_data>`, strip/escape bracketed numeric tokens so the model
  cannot echo them as `[N]` citations, truncate lengths, keep record ids for the
  UI panel.
- **rag.py:** when `personal_text` is empty, call `PROMPT.format_messages`
  exactly as today. When present, build
  `System(SYSTEM_PROMPT) → System(PERSONAL_DATA_RULES) → history →
  Human(personal block + context + question)`. Add the block to `trim_history`'s
  fixed cost.
- **app.py:** prefetch before `retrieve`; `effective_top_k = min(top_k,
  agent.personal_top_k_cut)` when personal data is used; store
  `personal_records`/`personal_caption` on the assistant message; render the
  "Your data" panel and freshness caption; stale warning + "Sync now"; sidebar
  sync subprocess (same pattern as re-ingest) and clear-data action.
- **Tests:**
  - intents classify (inventory/recipe/stock/create) and budget cap;
  - prompt with no personal text is byte-identical to baseline;
  - rules appear only when data exists, contain the injection rule, and do not
    ask for `[N]`;
  - bracketed tokens in personal data are neutralized;
  - freshness caption fields.
- **Exit:** with a fake DB, chat answers include personal context; with the
  integration disabled, existing tests and eval retrieval are unchanged.

### W4 — Capability detection + tools (1 day)

- **Files:** `agent/capabilities.py`, `agent/tools.py`, `agent/loop.py`,
  `tests/test_model_capabilities.py`, `tests/test_agent_tools.py`; edits to
  `app.py`, `rag.py` (expose message building), `config.yaml`, `config.py`.
- **Capabilities:** `POST /api/show` → `capabilities`; cache per model; explicit
  `models.<name>.supports_tools` wins; unreachable Ollama → `None` with a
  warning, not a crash.
- **Tools:** exactly three, flat required args, enums:
  - `search_my_data(scope, query?)` with `scope` in `inventory | recipes`;
  - `get_my_recipe(name)`;
  - `check_recipe_stock(name)`.

  Handlers return compact text wrapped as user data; errors become text. No
  write, sync, create, batch, or equipment tool exists. The tool path is only
  active when the integration is enabled and a DB exists; otherwise the chat
  runs today's path with no tool schemas.
- **Loop:** at most `max_tool_steps`; tool outputs wrapped; two invalid calls in
  a row disables tools for the session; final answer streamed.
- **app.py:** `st.status("checking your inventory…")`; support caption
  (`tools · detected via Ollama` or the warning); session degradation notice.
- **Tests:** capability parsing/override/unreachable; schema safety (no write
  tools, no nested args); loop limit; invalid-call fallback; tool errors are
  text; prefetch-only path when unsupported; no tool schemas when the
  integration is disabled.
- **Exit:** a tool-capable model can make one follow-up call; a non-capable
  model still answers from prefetch with a visible warning.

### W5 — Text intake, process plan, matching, stock check, Import tab (2–3 days)

- **Files:** `personal/extract.py`, `personal/plan.py`,
  `personal/matching.py`, `personal/stock.py`, `ui/import_tab.py`, tests.
- **Extract:** one LLM call with a process-aware strict-JSON schema (header
  including source attribution, fermentables, hops, yeasts, miscs with addition
  timing/use, `mash`, `boil`, `fermentation`, `packaging`, `process_notes`,
  `missing_fields`, `suggestions`). Use `ChatOllama(..., format="json")` with
  `intake.model`/`extraction_temperature`. Ingredient amounts live once, in the
  tables; `boil` and `packaging` hold duration/method only. One repair retry;
  raw text preserved; ranges validated; missing values null and flagged; no
  gap-filling. Alternative yeasts stay separate options. Source attribution is
  preserved as written.
- **Units:** convert deterministically to `intake.unit_system` (default metric;
  Brewfather is metric): gal→L, lb→kg, oz→g, °F→°C. Keep the original beside the
  converted value and mark it converted. Household units/fractions with no exact
  conversion stay raw with a null numeric amount and are flagged for editing.
  Conversion is unit math, not beer-stat arithmetic.
- **Plan:** deterministic `render_plan(spec)` ordering
  prepare → mash → sparge → boil → knockout/chill → ferment → package; only
  stated values, "not stated" for gaps.
- **Matching:** normalize (NFKD, synonyms, generic-token drop), exact → alias →
  token subset → `difflib` with distinctive-token guard; kind isolation;
  `unmatched` never guesses.
- **Stock:** `covered | short | untracked | unit_mismatch`; multiple lots; float
  tolerance; rank "what can I brew"; all arithmetic in code.
- **Detection + chat hook:** deterministic `looks_like_recipe(text)` (length +
  ingredient/process signals). On match in chat, run extraction outside the
  tool loop, render the plan, store the pending draft in session state, and show
  "Review draft"; the proposal replaces the free-form answer for that turn. A
  normal brewing question never triggers it.
- **Import tab:** paste text → draft + plan → `st.data_editor` tables → stock
  table → alias picker (writes alias only on confirm) → draft list/resume.
  Header form has name, type, batch size, boil time, efficiency, style — no
  equipment. Chat's "Review draft" preloads the same state.
- **No Brewfather:** extraction, plan, and local drafts work with the integration
  disabled. Stock says "Brewfather not connected / no inventory synced"; create
  is disabled with "Connect Brewfather to create this recipe."
- **Tests:** `test_ingredient_matching.py` (including `chocolate` vs
  `chocolate rye`), `test_stock_check.py`, `test_recipe_draft.py`,
  `test_recipe_text_intake.py`, `test_process_plan.py`,
  `test_recipe_detection.py`, `test_unit_conversion.py`.
- **Exit:** Phase 2 DoD — a magazine-style recipe pasted as plain text is
  parsed (including process steps), proposed back as an ordered plan, checked
  against real inventory when connected, edited, and saved as a validated draft;
  the same draft flow works without Brewfather; no write.

### W6 — Create-only write-back (0.5–1 day)

- **Files:** `integrations/brewfather/provider.py` (create), repository audit,
  `ui/import_tab.py`, `tests/test_write_invariant.py`.
- **Steps:** draft → exact payload preview (JSON + summary + account prefix) →
  explicit checkbox → single `POST /v2/recipes` → `write_audit` → show id/link.
  Disable the button on click; `status`/`brewfather_id` makes re-confirm a
  no-op; failure keeps the draft + API validation errors.
- **Payload:** header + ingredients always; `mash`/`fermentation` only if the
  live API shape is confirmed; source attribution goes into the recipe notes
  when present.
- **Guards:** `allow_writes` + `recipes.write`; client rejects non-allowlisted
  calls; no code path for PATCH/PUT/DELETE (assert with an AST/grep test).
- **Equipment:** payload omits `equipment`. Verify acceptance with a disposable
  recipe; if rejected, stop with a clear message and send nothing (no profile
  management, no invented default).
- **Tests:** allowlist; create refused without confirmation/flag; duplicate
  confirm no-op; failed POST recoverable; audit row content.
- **Exit:** Phase 3 DoD.

### W7 — Eval, docs, hardening (0.5 day)

- **Eval:** two opt-in files, both using the existing CSV schema; the normal
  `eval_questions.csv` runner is untouched.
  - `tests/eval_personal.csv` + `--personal` (Brewfather required):
    - "What hops do I have?" → in-stock hops, amounts, freshness, no citations;
    - "Do I have enough for my House IPA?" → coverage table, shortage named;
    - "What can I brew this weekend?" → ranked by coverage, never invents stock;
    - "Create this recipe in Brewfather" → asks for review/confirmation, no write.
  - `tests/eval_intake.csv` + `--intake` (no Brewfather needed):
    - A pasted magazine-style recipe → proposed recipe + ordered process plan,
      missing values flagged, no invented numbers, no write.
- **Docs:** README setup/scopes/sync/import/privacy; AGENTS.md §6.5;
  BREW_BOT_PLAN pointer.
- **Hardening:** grep/AST tests for forbidden methods; log-redaction test;
  `user_data` excluded from ingestion; no embedding calls in personal modules.
- **Exit:** `enabled: false` runs the entire existing eval and test set with no
  behaviour change, and the Import tab still extracts/plans/saves local drafts;
  the opt-in personal eval passes with a real key; `--intake` passes without one.

---

## 6. Test plan (matching repo conventions)

No pytest. Each file is a runnable script with `main()` and `assert`, run as:

```powershell
.venv\Scripts\python.exe tests\test_personal_db.py
```

New offline scripts:

| Script | Covers |
|---|---|
| `test_personal_db.py` | schema, WAL, upsert, soft delete, failed-sync preservation, clear-data |
| `test_brewfather_client.py` | paging, retry, 401/403, cap, redaction, allowlist |
| `test_brewfather_mapper.py` | fixtures → rows, units, null inventory |
| `test_write_invariant.py` | only `POST /recipes`; no PATCH/PUT/DELETE reachable; confirm/no-op |
| `test_ingredient_matching.py` | aliases, subsets, false-positive guards, unit mismatch |
| `test_stock_check.py` | statuses, lots, tolerance, ranking |
| `test_recipe_draft.py` | ranges, JSON repair, no gap-fill, create blocked |
| `test_recipe_text_intake.py` | prose extraction with a fake LLM JSON response, units, AAU/alpha, timing, yeast options, suggestions |
| `test_process_plan.py` | plan order, "not stated" gaps, no invented numbers |
| `test_recipe_detection.py` | long prose recipe triggers; normal question does not |
| `test_unit_conversion.py` | gal/lb/oz/°F → metric, original retained, household units flagged |
| `test_no_brewfather.py` | app/import path works with `enabled: false`; no personal prompt/tools |
| `test_agent_tools.py` | read-only set, loop cap, error-as-text, degradation |
| `test_model_capabilities.py` | parsing, override, unreachable |
| `test_personal_prefetch.py` | intents, caps, delimiters, bracket neutralization |
| `test_personal_prompt.py` | rules only with data, disabled-mode identity, no `[N]` requirement |

Regression gates (must stay green after every packet):

1. `test_model_selection.py` — model/config precedence unchanged.
2. `test_citations.py`, `test_style_disambiguation.py`, `test_style_pins.py`,
   `test_part_number_retrieval.py`, `test_expand_query.py`,
   `test_history_budget.py`, `test_refusal.py` — public path untouched.
3. `tests/run_eval.py --retrieval-only` — same top-k results with the
   integration disabled.
4. `tests/run_eval.py --intake` — text intake/plan works with no `.env`.
5. Manual: `streamlit run app.py` chat works with no `.env`.

---

## 7. Safety invariants → enforcement

| Invariant | Code mechanism | Test |
|---|---|---|
| No update/delete, ever | `(method, path)` allowlist + `allow_writes` gate in `client._request` | `test_write_invariant.py` |
| Model cannot write | all three tools read-only; no create/sync/adjust tool in `TOOL_SCHEMAS` | `test_agent_tools.py` |
| No network in chat path | only `provider`/`sync_cli` import `requests`; chat reads SQLite | grep/AST test |
| No personal data in Chroma | ingest reads `data_dirs` only; personal modules never embed | `test_personal_db.py` + grep |
| No secrets in logs/DB | redaction; env-only credentials; audit stores hash only | `test_brewfather_client.py` |
| No invented stock | coverage computed in code; "not tracked in Brewfather" rendering | `test_stock_check.py`, `test_personal_prompt.py` |
| Prompt injection | `<user_data>` wrapper, "data not instructions" rule, read-only tools, bounded loop | `test_personal_prompt.py`, `test_agent_tools.py` |
| Disabled mode identical | empty personal text path uses untouched `PROMPT` | `test_personal_prompt.py` + eval |
| Brewfather optional | prefetch/tools gated by the flag; Import tab local-only | `test_no_brewfather.py` |
| Clear data | synced tables + aliases + drafts/source text dropped; audit kept by default; credentials untouched | `test_personal_db.py` |

---

## 8. Suggested commit sequence

Implementation should start from `main` on a new branch per `AGENTS.md` (the
plan docs currently sit on `brewfather-integration-plan`; merge them first or
branch from that commit deliberately).

1. `chore: ignore user_data/.env`
2. `feat(personal): SQLite schema + repository`
3. `test(personal): db tests + synthetic Brewfather fixtures`
4. `feat(brewfather): allowlisted client + mapper`
5. `feat(brewfather): sync provider + CLI`
6. `feat(personal): prefetch + prompt personal section`
7. `feat(ui): sidebar sync + Your data panel + freshness`
8. `feat(agent): capability detection + bounded read tools`
9. `feat(personal): matching + stock + text intake + process plan`
10. `feat(ui): Import tab + draft review`
11. `feat(brewfather): create-only write-back + audit`
12. `test(eval): opt-in personal eval + rows`
13. `docs: README/AGENTS/BREW_BOT_PLAN`

---

## 9. Risks introduced by this integration

| Risk | Mitigation |
|---|---|
| Prompt regression when personal section is appended | separate `PERSONAL_DATA_RULES`; empty-path identity test; top_k cut only when used |
| History budget overflow | personal text added to `trim_history` fixed cost + 4500-char cap + 8→4 top-k cut |
| `normalize_citations` eats bracketed text from recipe names | neutralize `[digits]` in `personal/render.py` before prompting |
| Streamlit `st.data_editor` row identity across reruns | stable draft row ids; `num_rows="dynamic"`; persist spec after every edit |
| Subprocess sync imports fail under `python -m` | CLI entry point + `sys.path` insert like `tests/*`; tested from repo root |
| `.env` not visible to the sync subprocess | `load_dotenv()` in `config.py`, imported by every entry point |
| Duplicate recipe on double confirm | disabled button + draft status check inside the transaction |
| Ollama metadata disagrees with reality | explicit `supports_tools` override + session degradation after 2 invalid calls |
| Create rejected without `equipment` | W6 disposable-recipe check; stop with a clear message, send nothing |
| Prose extractor misses/merges process steps | process-aware schema + deterministic plan + source text beside draft + synthetic fixtures |
| Extractor picks one yeast silently | alternative yeasts stay separate options; UI requires a choice |
| Extractor invents temperatures/times/amounts | grounded-only prompt, null + `missing_fields`, separate `suggestions`, tests |
| Long pasted recipe exceeds context | `intake.max_source_chars`, explicit truncation warning, never silent |
| Unit conversion error | pure functions + tests; original retained beside converted value; user-editable |
| Optional integration changes core chat | prefetch/tools gated by the flag; no-`.env` smoke test; empty-path prompt identity test |
| Rate-limit exhaustion | one client chokepoint, TTL, 60-request cap, 429 backoff, cached DB keeps chat working |

---

## 10. Resolved defaults (2026-11-26)

### 10.1 Product decisions

| # | Decision | Resolution |
|---|---|---|
| 1 | Units | Convert deterministically to `intake.unit_system` (default metric; Brewfather is metric): gal→L, lb→kg, oz→g, °F→°C. Keep the original beside the converted value and let the user edit. No per-recipe prompt; the config default applies. Household units with no exact conversion stay raw, numeric amount null, flagged. |
| 2 | Chat proposal content | Extracted facts + deterministic plan; model advice only on follow-up. |
| 3 | Process plan editability | Read-only steps + editable notes. |
| 4 | Clear personal data | Drops synced tables, aliases, and drafts/source text by default; hash-only audit kept unless the user opts to clear it. Credentials untouched. |
| 5 | Brewfather disabled | Public chat unchanged; text intake, plan, and local drafts work; stock says "Brewfather not connected / no inventory synced"; create disabled with a clear message. |
| 6 | Extraction model | Selected chat model by default; `intake.model` pins another model. |
| 7 | Create process blocks | Include `mash`/`fermentation` when the live API shape is confirmed; otherwise keep the plan local-only. |
| 8 | Household units | Preserve raw text, numeric amount null, flag for user edit. |
| 9 | Chat-pasted draft persistence | Session state only until "Review draft"; the explicit review creates the DB draft. |
| 10 | Source attribution | Preserve as written; put it in the Brewfather recipe notes. |

Items 5–10 are MVP defaults and can be revisited later; they are not blockers.

### 10.2 Live API checks (spike/W6, not user decisions)

- API access on the account's plan and the exact key scopes.
- `updatedAt` vs `_timestamp` field name.
- `POST /v2/recipes` accepts a recipe without `equipment`.
- `POST /v2/recipes` accepts `mash`/`fermentation` blocks (only if we include them).
- `POST /v2/recipes` accepts `notes` for source attribution.
- Live inventory payload edge cases match the synthetic fixtures.

---

## 11. Definition of done (condensed)

- **Phase 1:** chat answers inventory and recipe questions from SQLite with
  freshness and no `[N]` on personal facts; sidebar sync works; non-tool model
  degrades visibly; integration disabled is the current app unchanged.
- **Phase 2:** pasted text recipe (prose steps included) → validated draft +
  ordered process plan → editable → coverage table → saved draft; missing
  values and suggestions handled explicitly; no write.
- **Phase 3:** preview → confirm → exactly one new Brewfather recipe; audit row;
  re-confirm no-op; no PATCH/PUT/DELETE path exists.
