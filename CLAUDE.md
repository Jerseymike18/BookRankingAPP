# CLAUDE.md — The Reading Ledger

Standing rules for working in this repository. **Why** each rule exists — the measurements,
incidents and design arguments — lives in [`docs/engineering-notes.md`](docs/engineering-notes.md),
under the section named in brackets. Read that section before reworking anything a rule
guards. Read `ARCHITECTURE.md` (run-modes, deployment map, codebase map) before scoping work.

## What this is

A book-tracking and prediction app calibrated to one reader's taste: every read book is rated
on 14 components and the engine predicts enjoyment of unread ones. Three env-switched
run-modes, both live sites building from **`main`**:

- **Local dev** — read/write, SQLite `books.db`, no auth. `bash start.sh` → frontend
  http://localhost:3000, API http://localhost:8000.
- **Public showcase** — static read-only snapshot (`NEXT_PUBLIC_STATIC_DATA=1` +
  `NEXT_PUBLIC_READONLY=1`) on Vercel, built by the export + git-hook pipeline.
- **Hosted app** — `www.thereadingledger.com`, multi-tenant, Supabase auth + Railway + Postgres.

Stack: FastAPI/uvicorn backend (`backend/main.py`), Next.js App Router + Tailwind v4
(`frontend/`), pure-Python engine (`db_loader.py`, `predict_engine.py`, `views.py`,
`db_write.py`), Anthropic Claude for research. The API key loads from `apikey.txt` (via
`research_layer.load_key`; `apikey.py` is the alternate) — both untracked.

## HARD CONSTRAINTS — do not violate

1. **Never touch prediction/derived math.** `predict_engine.py`, `db_loader.py` and
   `views.py` are read-only reference implementations; callers never reimplement or
   duplicate their logic (WA, tier banding, series Adjusted WA). The one exception is the
   **series-quality model**, owned by `views.py`: change it only on explicit request and
   re-run `test_series_score.py`. (`db_loader`'s `"Series #"` column is a read-only
   passthrough.) Commits that move engine files need an `engine-change: <reason>` line.
2. **All writes go through `db_write.py`.** No direct SQL anywhere else. Use the existing
   validated functions only (e.g. `add_book`, `change_rating`, `delete_book`,
   `set_year_read`, `set_status`, `update_queue`, `add_recommendation`,
   `set_recommendation_meta`, `update_recommendation_scores`, `update_book_metadata`,
   `set_series_number`, `set_done`, `set_score_anchors`, `reset_score_anchors`,
   `set_series_complete`, plus the nonfiction equivalents — including
   `update_nonfiction_recommendation_scores`). Do not add write functions unless asked.
3. **The DB schema is fixed.** `books`: `title, genre, author, series, series_number, words,
   year_read, status` + the 14 components (+ nullable `read_month`). `recommendations`: the
   components + `series_number, done, blurb, keywords`. New columns/tables only via an
   explicit, owner-authorized schema task through `db_write`. Authorized additions so far:
   `series_meta` (series completeness — **no row = not complete**, which is load-bearing),
   `profiles`, `score_anchors`, the weight-override tables, `reading_checkpoints`
   (mid-book reviews — display only, the engine never reads it), and the operational
   `app_state` / `rate_limit_hits`.
4. **`test_engine.py` must stay at a clean pass — currently 38/38, no `[FAIL]` lines.** The
   DB is the source of truth; Excel/DB drift prints as informational, not a failure.
5. **No new visual styles.** Extend the "Fable" tokens in `frontend/app/globals.css` and
   reuse existing primitives (`.wa-badge`, `.book-card`, `.genre-chip`, `.comp-tile`, the
   `SegmentedTabs`, the app shell in `components/Nav.tsx`, `--shadow-card`, tier spine
   colors). No new colors, fonts or component patterns where one exists. The palette is
   "Fable, turned up" (owner-chosen 2026-11-05); change it only on explicit request.

Rule of thumb: if it changes a number, it's math (read-only); if it changes how an existing
number is shown or sorted, it's presentation (fair game).

## Scoring model (reference — implemented in the engine, never reimplemented)

14 components, 5 categories, each 0–10:
**Story** (Plot, Entertainment, Action, Ending) · **Character** (Depth, Emotional Impact,
Motivations) · **Aesthetics** (Prose, Narration) · **Theme** (Insights,
Thought-Provokingness) · **Worldbuilding** (Depth2, Integration, Originality).

- **WA** = genre-weighted sum of category averages (`genre_weights`, `component_weights`,
  plus per-user overrides). Everything sorts by WA.
- **Total Average** = unweighted mean of the five category averages — tier bands and series.
- **Tier bands:** S+ fixed at Total Average ≥ 9.5 (≥ 9.0 for series); the rest split
  S/A/B/C/D/F by percentile (~9/15/25/25/15/10%). Spine colors: S+ #0B6E4F · S #1C8355 ·
  A #5DBB63 · B #F2B134 · C #EE7B4A · D #5B8DEF · F #B9AFA6.
- **Stored 0 = "not scored"** (rendered "—"; masked to NaN in Total Average).
- **Nonfiction** is a separate 12-component / 5-category schema with no Worldbuilding and no
  correction layer.

**Worldbuilding mask** [→ *The worldbuilding mask*]. A genre whose effective (per-tenant)
Worldbuilding weight is 0 gets no worldbuilding score:
- Read side (`genre_affinity.py`) masks to **`None`**; predict side
  (`research_predict.mask_worldbuilding`) masks to **`0.0`**. The difference is deliberate.
- The predict-side mask runs in exactly one place — `correct_and_predict`, **after** the
  0–10 clamp and **before** the WA roll-up. Never add a second copy at a call site, and
  never move it ahead of the smoothing. Guard: `test_worldbuilding_mask.py`.
- It keys off effective weights, never a hardcoded genre list, and fails closed.
- `scripts/lint_data.py` WARNs one way only: a WB-weighted genre holding zeroes is allowed.

**Series score** (`views.series_quality_terms` / `series_aggregate`) [→ *Key formulas*]:
`avg_WA + clamp(Consistency + Peak + Finale + Unfinished, ±0.75) + Evidence`.
- **No length/commitment term — never reintroduce one** (the owner finishes every series).
- Consistency K = 0.50; Finale applies only to series marked complete; Unfinished −0.10;
  Evidence −0.4 at n=1, outside the clamp.
- `complete` is **tri-state**. `None` (no `series_meta` supplied) means no Finale **and** no
  Unfinished charge.
- Guard: `test_series_score.py`. The `/series-breakdown` page reads its coefficients live
  from `/api/engine-parameters`.

## Prediction rules

**Served interval = the conformal 80% band, nothing else** [→ *Prediction intervals*].
`intervals.py` maps same-author analog count → half-width from `calibration/residuals.json`;
it is omitted (never invented) when no table is loaded. Measured coverage: **81.4%** on
honest errors in `validation/interval_coverage.md`, and 82.7% in the 2026-09-07 walk-forward.
80% is the owner's choice — don't re-inflate to 90%.
- **Never** serve a `resid_sd`-derived "90%/95% CI" in any response, page or LLM prompt.
  `resid_sd` is a regression fit diagnostic only (calibration page, the repredict
  noise-floor gate).

**Models.** Grounded research = `RESEARCH_MODEL` (`claude-opus-4-8`); Discover and
classification = `DISCOVER_MODEL` (`claude-sonnet-4-6`). One named constant per pipeline.
- `research_cache.json` is keyed by title+author. It is never auto-purged and is not
  invalidated by a model switch.
- A bulk re-research on a new model means the corrections must be refit against that
  model's biases.

**Rating-scale anchors** [→ *Rating-scale anchors*]. Per-user anchors are a **remap** of the
raw research vector, applied before smoothing and correction.
- **Never** inject anchors into the prompt or key the research cache per user.
- Never apply them on `/api/predict/instant` or to stored components.
- The correction ladder trains on canonical raw scores.
- Default = identity (byte-identical output).
- Guard: `test_score_anchors.py`, which includes a prompt-drift check against `rm.ANCHORS`.

**Re-prediction** [→ *Auto re-prediction*]. `repredict_on_add.py` is the only automatic
re-prediction path: same author always, same-genre peers past the noise gate, per-add cap
with deferrals reported. It writes via `update_recommendation_scores` + `log_delta`.
- `repredict_one` and `ground_saved_rec` share `_predict_rec`. Keep one prediction core.
- Audit rows are tagged `baseline_repredict:…`. That prefix is what
  `delta_log_view._REPREDICT_PREFIX` filters, so never retag without updating it.
- The report applies the cold-start term to both old and new, gated on **`rank_pool`
  (own-library) counts**, so it agrees with the read-queue.
- **Nonfiction re-predict is a different operation — don't unify the two.** It always forces
  a fresh Opus call (confirm-gated), writes no `delta_log` row, has no interval, and ranks
  by WA.
- Re-predict noise: WA is stable; fiction **rank is not**. Never read a rank move from a
  re-predict as signal.
- Guard: `test_repredict_one.py`.

**Cold start.** `rank_pool` is always the reader's own library, never the borrowed seed —
the rank-leak guard. `MIN_LIBRARY_BOOKS = 15` is the one "library big enough" threshold.

**Genre Prediction** (`genre_affinity.py`, `/predict/genres`, fiction only, read-only over
the engine) [→ *Genre recommendation*]:
- Affinity is a **shrunk** mean with a band. That band is never presented as the served
  interval.
- Surprise uses engine-produced forecast rows only. **Exclude the workbook backfill.**
- Every figure in the response is copied from the evidence dict, never from model prose.
- The LLM guards fail closed: drop picks outside `genre_weights`, and drop "types" that state
  a decimal.
- Page state lives in the job provider, persisted under the same `STORAGE_KEY` as the runs
  (cleared on sign-out). `loading`/`error` are not persisted, and restored state is
  shape-checked.
- Guard: `test_genre_affinity.py`.

## Serving & infrastructure rules [→ *Serving latency*, *Multi-worker*]

- **Cold-start term is stale-while-revalidate**, keyed on `_engine_epoch`.
  - Never reintroduce `_cold_term_cache.pop(...)` into `_invalidate_engine`.
  - No previous fit → fit synchronously. A failed refit keeps the last good value. A `None`
    fit is cached as `None`.
  - Guard: `test_cold_term_cache.py`.
- `/api/read-queue?blurbs=0` is for the read-queue page only. Blurbs lazy-load keyed on
  `blurb === undefined`, never on a mode flag. `keywords` stays inline.
- `proxy.ts` verifies JWTs locally (`getClaims`). The welcome wizard must `refreshSession()`
  after `updateUser()`.
- **DB round trips are the unit of latency.**
  - Pool `minconn` must never be 0.
  - Reads run in scoped autocommit (`db_backend.readonly()`). Never use blanket autocommit,
    or the multi-statement writers lose atomicity.
  - Query traffic goes to the transaction pooler (`:6543`). The `cache_sync` LISTEN and
    `CrossProcessLock` must stay on the session pooler (`session_dsn()`); transaction mode
    silently drops LISTEN.
  - The session pooler caps at 15 clients, so pool size derives from `DB_MAX_CLIENTS`.
- **Workers:** set the count with `LEDGER_WORKERS` (Procfile). Never use `WEB_CONCURRENCY`
  for this; the Procfile exports it from `LEDGER_WORKERS`. Check via `GET /api/version`.
- **Cross-worker cache invalidation** uses `cache_sync` NOTIFY plus a 10s reconciliation
  sweep. Its callback must call the `_local` invalidators only, or the workers loop.
- **Rate limits:** buckets that gate money are shared in Postgres (`llm`, `demo_live`,
  `demo_live_global`, `signup`). The rule is the paid **call**, not the endpoint, so
  `_build_author_prior` is metered on `llm` and fails closed. Other buckets are in-memory,
  with the budget divided by worker count.
- Local dev (SQLite) never writes `app_state` / `rate_limit_hits`, so it doesn't churn the
  tracked `books.db`.
- Guards: `test_multiworker.py`, `test_author_prior_metering.py`.
- Don't reintroduce `_show_computed_wa` on the write path (TTY-gated) or `_backup_once` on
  Postgres.

## Security posture

- **Local/showcase:** single-user, no auth. uvicorn binds `127.0.0.1` only — never
  `--host 0.0.0.0` without auth plus an endpoint audit. CORS is `http://localhost:3000`
  unless `ALLOWED_ORIGIN` is set. Never put this behind a public proxy without auth.
- **Hosted:** `AUTH_ENABLED=1`. Tenant = JWT `sub`, isolated by `WHERE user_id=?`
  everywhere (app-layer, no RLS). Invite-gated sign-up.
- The CSP `connect-src` lists localhost origins **in the `isDev` branch only**.
- **Middleware order:** `_cors_safe_errors` must be registered *before* `CORSMiddleware`.
- **Public profiles** are the one deliberate cross-tenant read. `_resolve_public_target`
  returns 404 for missing **or** private handles, and the cross-user routes are thin
  delegations to tenant-scoped handlers. Guards: `test_public_profiles.py`,
  `test_tenant_scope.py`.
- **Secrets:** `apikey.txt` / `apikey.py` stay untracked. Never commit, print or echo a key.
  Check staged new files before committing.
- **`books.db` drifts from live** (hosted Postgres is the source of truth). Run
  `python3 scripts/refresh_from_live.py` (dry run) before analysing the owner's library,
  and `--write` before any claim about what they've rated, own or are missing. An
  unrefreshed copy is fine for exercising code. If you can't refresh, say the claim is
  unchecked.

## Frontend rules

- **Reading status is server-derived.** Fiction uses the `read_queue` order; nonfiction uses
  `nonfiction_books.status`. Never add a browser-local reading status. The only
  `localStorage` is the mute preference in `lib/notify.ts`; everything else is sessionStorage.
- **Drafts** (`lib/draft-storage`; `/welcome`, `/add-book`) [→ *Unsaved work*]:
  - Every key is declared in `DRAFT_KEYS` so sign-out clears it.
  - `discard()` ≠ `clear()`: `clear()` latches the slot off for the rest of the page's life.
  - The mirror effect is declared **before** the hydrate effect.
  - A restore is shape-checked against current server truth.
- **Welcome wizard** commits each window as the reader goes. Only `finish` sets `onboarded`.
  Continue and `finish` share the same idempotent commit functions. The wizard clears only
  the overrides it created.
- **Goodreads import:** classification is capped (`IMPORT_ENRICH_MAX`). The deferred count
  is surfaced, and `POST /api/import/enrich` re-runs it on the `llm` bucket. Never make the
  re-run automatic.
- **Library export** (`shelf_export.py`) [→ *Library export*]:
  - Stars use a linear half-up mapping, `clamp(floor(score/2 + 0.5), 1, 5)` — never
    `round()`.
  - A predicted score never becomes a star, and missing means an empty rating.
  - The exact score goes in **private** Notes, never `My Review`.
  - Guard: `test_shelf_export.py`.
- **Nav:** `activeItemHref` picks the longest match (Predict's hrefs nest).

## Pages (frontend/app/)

Top-level: `add-book` · `edit-ratings` · `predict` (+ `predict/genres`) · `read-queue` ·
`stats` · `analytics` · `calibration` · `track-record` · `methodology` · `series-breakdown` ·
`delta-log` · `weights` · `welcome` · `login` · `import` · `try` · `directory` · `profile` ·
`u` (`/u/[handle]` public profile), plus the `/` home. `tier-list`, `series`, `reading` and
`timeline` each carry an in-page fiction/nonfiction toggle over `components/views/*View.tsx`.
`/rankings` redirects to `/stats#rankings` (`next.config.ts`).

Nav: `components/Nav.tsx` · API: `lib/api.ts` · types: `lib/types.ts` · read-only gating:
`lib/readonly.ts` + `lib/readonly-context.tsx`. A new page must be added to this list — the
pre-push doc lint (`scripts/lint_docs.py`) fails otherwise.

## Publishing

The public site is a static snapshot, so **a data commit IS a publish.** Hooks live in
`scripts/hooks/` (`scripts/setup-hooks.sh`; `core.hooksPath` = `scripts/hooks`):

- **pre-commit** regenerates the snapshot (`scripts/export_static_data.py`) and stages it.
- **pre-push** re-checks it with `--check` and blocks a stale snapshot.
- Both run the data lint, and ERROR findings block. Owner-pending excusals go in
  `scripts/lint_allowlist.json`.
- `_path_is_data_input` must cover every snapshot input: `books.db`, `validation/*`,
  `calibration/*`.
- `start.sh` runs `scripts/autopublish.sh`, which auto-commits `books.db` edits. Never
  `--no-verify`. Details are in `README.md`.

## Working rhythm

- One feature per commit. Verify the affected page still works and `test_engine.py` is
  clean, then commit with a descriptive message.
- **Frontend tests:** `cd frontend && npm test` (vitest, node env, pure logic only — no
  jsdom until a component genuinely needs it).
  - CI runs in `.github/workflows/frontend-tests.yml`. It is a required check alongside
    `lint-constraints`, and **must never gain a `paths:` filter** (a skipped required check
    never reports). The filtering lives in the job, fails safe toward running, and excludes
    `frontend/public/data/**`.
  - Don't rename `lint-constraints` or add trigger filters to `constraints.yml`.
  - Frontend tests are not run in the local pre-push hook.
- `cd frontend && npx eslint .` stays at zero errors and zero warnings.
- **Two derived artifacts go stale on different triggers.** Each hashes its own inputs —
  never compare the two hashes:
  - `calibration/residuals.json` (`intervals.engine_hash`: `predict_engine.py` +
    `validate_engine.py`). Regenerate with `python3 validate_engine.py --write-residuals`.
  - `validation/walkforward_*` (`intervals.backtest_engine_hash`: `predict_engine.py`,
    `db_loader.py`, `reresearch_and_measure.py`, `research_predict.py`). Regenerate with
    `python3 walkforward.py` (zero-spend, deterministic).
  - The `backtest-stale` lint WARNs until it is re-run. Re-running changes public figures,
    so it's the owner's call.

## Validation [→ *Walk-forward validation*]

`walkforward.py` is the chronological backtest: each fold trains only on books read before
it, with cache-only, zero API spend. Variants: raw / honest / hybrid / leaky. The honest
walk-forward WA MAE baseline to beat is **0.589** (2026-09-07, 127 folds). Hybrid is the
served headline, and leaky is labelled leaky everywhere. The retired DeltaTracker
(`component_corrections`) layer enters no variant and must never be described as active.

Pages fed from it:
- **`/track-record`** — tenant-scoped `delta_log` via `delta_log_view.visible_rows`.
  Coverage goes through `intervals.interval_for`.
- **`/methodology`** — tenant-scoped `/api/engine-parameters` plus the global
  `/api/engine-validation`. Its numbers are read live.
- **Anti-drift rule:** when engine math changes, make sure the new value surfaces through
  `/api/engine-parameters`, and re-read the hand-written prose for conceptual changes.
