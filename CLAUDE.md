# CLAUDE.md — The Reading Ledger

Standing instructions for working in this repository. Read before making any change.

## What this project is

A personal book-tracking and prediction web app. The owner rates every book they read
across 14 fine-grained components, and the app predicts how much they'll enjoy any future
book before reading it. It is a precision instrument calibrated to one person's taste — not
a discovery service, not multi-user. The full read/write app runs on localhost; a
**read-only static snapshot** of it is published to the public web (Vercel, built with
`NEXT_PUBLIC_STATIC_DATA=1` + `NEXT_PUBLIC_READONLY=1`) via a deterministic export + git-hook
pipeline — see **Publishing** below and `README.md`.

> **Current state (read `ARCHITECTURE.md` first).** The same codebase now also runs as a **hosted
> multi-tenant web app** (`www.thereadingledger.com`, Supabase auth + Railway/Postgres) alongside
> the public read-only showcase — **three env-switched run-modes** (local dev / public showcase /
> hosted app), both live sites building from **`main`**. `ARCHITECTURE.md` has the run-modes,
> deployment map, and codebase map; consult it before scoping work.

## Stack

- **Backend API:** FastAPI (Python), uvicorn, SQLite via `books.db`
- **Frontend:** Next.js (App Router), Tailwind CSS v4
- **LLM:** Anthropic Claude; the API key loads from `apikey.txt` (the canonical source, via
  `research_layer.load_key`), with `apikey.py` as an alternate key file. Both untracked — see Secrets.
- **Data engine:** pure Python (`db_loader.py`, `predict_engine.py`, `views.py`, `db_write.py`)
- **Launch:** `bash start.sh` from project root (starts both servers)
- **URLs:** Frontend http://localhost:3000 · API http://localhost:8000

## HARD CONSTRAINTS — do not violate

1. **Never touch prediction/derived math.** `predict_engine.py`, `db_loader.py`, and
   `views.py` are read-only reference implementations. The frontend and endpoints call them;
   they never reimplement or duplicate their logic. WA computation, tier banding, and series
   Adjusted WA all live here and stay here.

2. **All writes go through `db_write.py`.** Never write direct SQL to the database from the
   backend or anywhere else. Use the existing validated functions only (e.g.
   `add_book`, `change_rating`, `delete_book`, `set_year_read`, `set_status`,
   `update_queue`, `add_recommendation`, `set_recommendation_meta`,
   `update_recommendation_scores`, `update_book_metadata`, `set_series_number`, `set_done`,
   `set_score_anchors`, `reset_score_anchors`, plus the nonfiction equivalents). Do not add
   new write functions unless explicitly asked.

3. **DB schema is fixed.** The `books` table has `title, genre, author, series,
   series_number, words, year_read, status` plus the 14 component columns. `recommendations`
   has the same components plus `series_number, done, blurb, keywords`. Do **not** add columns
   without an explicit schema-change task that goes through `db_write`.

4. **`test_engine.py` must stay at a clean pass (every check PASSES, no `[FAIL]` lines —
   currently 38/38).** The DB is the source of truth; the Excel workbook is import-only, so
   Excel/DB drift is expected and is printed as informational (not pass/fail). Any `[FAIL]`
   line means something broke — investigate before proceeding.

5. **No new visual styles.** All UI extends the existing design tokens in
   `frontend/app/globals.css` (the "Fable" system) and reuses existing primitives
   (`.wa-badge`, `.book-card`, `.genre-chip`, `.comp-tile`, the SubTabs pattern,
   tier spine colors). Do not introduce new color values, fonts, or component patterns
   where a token or primitive already exists.

6. **Reading status for unread books is localStorage-only.** The `recommendations` table
   has no status column. Currently-reading / reading-next for TBR books persists in the
   browser only. The ordered **queue** itself is different — it persists via the
   `read_queue` table through `update_queue`. Don't conflate the two.

## Scoring model (reference)

14 components in 5 categories, each scored 0–10. Worldbuilding is optional (0) for realist genres.

- **Story:** Plot, Entertainment, Action, Ending
- **Character:** Depth, Emotional Impact, Motivations
- **Aesthetics:** Prose, Narration
- **Theme:** Insights, Thought-Provokingness
- **Worldbuilding:** Depth2, Integration, Originality

**Weighted Average (WA)** is the primary ranking score — a genre-weighted sum of category
averages, using weights in the `genre_weights` and `component_weights` tables. WA is what
everything sorts by. **Total Average** is the unweighted mean of the five category averages —
used for tier bands and series aggregation.

## Prediction intervals (served) — the conformal 80% band, not `resid_sd`

The interval shown on the Predict and Read-queue pages (and exported to the public
snapshot) is the **density-bucketed conformal 80% band**: `intervals.py` maps a book's
same-author analog count to an empirical half-width from `calibration/residuals.json`
(built by `validate_engine.py --write-residuals`). It is walk-forward-validated at
**81.4%** coverage on the honest error set (`validation/interval_coverage.md`), widens
as the analog pool thins, and is omitted entirely — never invented — when no residual
table is loaded.

It is **not** `±1.645·resid_sd`. `resid_sd` is the residual of the near-deterministic
WA-from-category-averages regression (R²≈0.99) — a fit diagnostic, not an unread-book
prediction interval; that band covered only ~31% of honest errors while claiming 90%.
`resid_sd` is retained **only** as that regression diagnostic (calibration page; the
`repredict_on_add` noise-floor gate), never as a served interval.

- **Coverage is 80% by choice** (owner decision, 2026-07-07): keep the honest,
  well-calibrated 80% band rather than re-inflate to a nominal 90%.
- **Regression guard:** never reintroduce a `resid_sd`-derived "90%/95% CI" into any
  served response, page, or LLM prompt — the conformal band is the only served interval.

## LLM model usage

- **Grounded research** (14-component rubric scoring, the calibrated path): **`claude-opus-4-8`**.
  Chosen after a full-library A/B test — Opus recenters raw bias toward zero on the
  interpretive components (Depth, Motivations, Emotional Impact) and helps most on literary
  genres. Leave the rubric and the author/genre correction math unchanged.
- **Discover mode** (candidate generation, brainstorming throughput): **`claude-sonnet-4-6`**.
  No calibration benefit from Opus here; keep it cheap.
- Prefer a single named constant per pipeline (`RESEARCH_MODEL`, `DISCOVER_MODEL`) so model
  swaps are one line.
- `research_cache.json` is keyed by title+author. Switching models does NOT invalidate cached
  entries — old results serve until explicitly re-researched. Never auto-purge the cache.
- **DeltaTracker note:** correction weights were learned against the model's raw biases. If the
  cached corpus is ever bulk re-researched on a new model, the DeltaTracker corrections should be
  recomputed against the new model's biases — otherwise old-model corrections get misapplied.

## Rating-scale anchors (per-user prose→number scale)

The research prompt converts reader sentiment into 0–10 component scores through a fixed
table of seven bands (`reresearch_and_measure.ANCHORS`: "really strong / recommend it" →
8.0–8.5, and so on). Those numbers were one reader's judgement, so each tenant can set their
own — `score_anchors.py` (read side + the remap) over the per-user `score_anchors` table
(`db_write.set_score_anchors` / `reset_score_anchors`, LONG format, sparse-by-user like the
weight overrides), edited in the **fourth window of the `/welcome` wizard** and served by
`GET/PUT /api/score-anchors` (+ `POST /api/score-anchors/reset`).

- **It is a REMAP, not a per-user prompt.** The prompt and the research cache stay canonical
  for everyone (a book is researched once, ever, for all tenants); the reader's anchors are
  applied afterwards as a monotone piecewise-linear map of the raw vector — canonical band
  centre → their centre, top-segment slope extrapolated above the highest anchor, clamped to
  0–10. Zero extra LLM spend, no cache fragmentation, instant effect, exactly reversible.
  **Never** re-key the research cache per user or inject anchors into the prompt.
- **Where it applies:** the raw research scores only, *before* correlation smoothing and the
  author/genre correction — backend `_build_research_response` (so `/api/predict/research`
  and the `/try` demo can't drift), the nonfiction predict path
  (`nonfiction_research.research_and_predict(anchors=…)`), and both `repredict_on_add`
  paths. Never on `/api/predict/instant` (analog-only, no LLM vector) and never on stored
  components (already remapped when predicted).
- **The correction ladder keeps training on canonical raw scores.** Remapping both the target
  and the training pairs would let the per-genre refit silently undo the reader's choice.
  Same reasoning as the DeltaTracker note above: corrections are calibrated against the
  model's raw biases.
- **Default = identity, and that is a regression guard.** With the canonical table
  `remap_scores` returns its input unchanged, so every existing prediction, the walk-forward
  baseline, and `test_engine.py` are byte-identical. `test_score_anchors.py` (28 checks) is
  the gate: identity, monotonicity/bounds, the write gate (partial/inverted/out-of-range
  rejected), tenant isolation, and a **prompt-drift check** that every band label still
  appears verbatim in `rm.ANCHORS` — keep them in sync when either changes.

## Auto re-prediction

`repredict_on_add.py` is the sanctioned automatic-reprediction path. When a book is
added/finished (backend `POST /api/books`, run in the background; the client polls
`GET /api/repredict/recent`), it re-predicts the unread recommendations whose baseline moved:
**the same author always**, and **same-genre peers only when the genre baseline shifts past a
noise-floor gate** — bounded by a per-add cap, with any deferred peers reported (never silently
capped). It writes through `db_write.update_recommendation_scores` + `log_delta` (rows tagged
`baseline_repredict:*`) and supports a dry-run. It calls the read-only engine; it never
reimplements or mutates prediction math.

## Security posture

The app runs in **two postures** (full detail in `ARCHITECTURE.md`):

- **Local dev + public showcase — single-user, no auth.** Local dev binds to loopback with
  `AUTH_ENABLED` off (every request → `db_backend.DEFAULT_USER_ID` = Michael); the public showcase
  is a backend-free static read-only snapshot. The rules below govern this posture.
- **Hosted app — auth ENFORCED.** On Railway with `AUTH_ENABLED=1`, every request must carry a
  valid Supabase JWT (tenant = token `sub`, verified in `auth.py`); per-user isolation via `user_id`
  on the 7 tenant tables; `ALLOWED_ORIGIN` locked to the app domain; sign-up is invite-gated
  (`signup.py`). This is the "expose on a network" path below, done properly.

Rules for the **unauthenticated (local / showcase) posture:**

- Write/delete endpoints are unauthenticated when `AUTH_ENABLED` is off. That is safe on loopback.
- CORS is locked to `http://localhost:3000` by default (env var `ALLOWED_ORIGIN` to override).
- uvicorn must bind to `127.0.0.1` (the default). Never pass `--host 0.0.0.0` without
  first adding authentication and reviewing every write/delete endpoint.
- Do not put this behind a public reverse proxy without auth.

If you ever need to expose this on a network, the minimum steps are: add an auth layer
(e.g. HTTP Basic + TLS, or a token middleware), set `ALLOWED_ORIGIN` to the real frontend
URL, and audit every unprotected endpoint in `backend/main.py`.

## Publishing

The public site is a **read-only static snapshot**, not a running backend — so **a data commit
IS a publish.** The git hooks in `scripts/hooks/` (activate per-clone with
`scripts/setup-hooks.sh`; `git config core.hooksPath` must read `scripts/hooks`) drive it:

- **pre-commit** regenerates the snapshot from the staged `books.db`
  (`scripts/export_static_data.py`) and auto-stages it into the same commit.
- **pre-push** re-runs the export in `--check` mode and blocks the push if the snapshot is
  stale or invalid.
- Both paths run the data lint (see **Working rhythm**), so an ERROR-level data problem blocks
  the publish — nothing ships broken.

`bash start.sh` also launches a watcher (`scripts/autopublish.sh`) that silently commits + pushes
`books.db` edits (debounced; `books.db` + snapshot only). Don't bypass the hooks with
`--no-verify`. Full details live in `README.md` — don't duplicate them here.

## Secrets — critical

- `apikey.txt` and `apikey.py` are **untracked** (in `.gitignore`) and must stay that way.
  Never commit, print, echo, or paste an API key. Never add a file containing a key to Git.
- Before any commit that stages new files, sanity-check nothing secret is included.

## Key formulas (reference — implemented in the read-only engine, do not reimplement)

- **WA:** weighted sum of `WStoryAvg × Story% + WCharAvg × Char% + WThemeAvg × Theme% +
  WAesAvg × Aes% + WWBAvg × WB%` per genre weights.
- **Series Adjusted WA:** `avg_WA + 0.0582 × (1.18^(n−1) − 1) − max(0, 3−n) × 0.2`
  (length bonus above 1 book; short-series penalty below 3).
- **Tier bands:** S+ fixed at Total Average ≥ 9.5 (≥ 9.0 for series); remaining books split
  S/A/B/C/D/F by percentile (~9% / 15% / 25% / 25% / 15% / 10%).
- **Tier spine colors:** S+ #2D6A4F · S #4A7C59 · A #7BA87B · B #D4A853 · C #C07C5A ·
  D #7B8FA1 · F #C4B8AD.

## Pages (frontend/app/)

Top-level: `add-book` · `edit-ratings` · `predict` · `read-queue` · `stats` ·
`analytics` · `calibration` · `track-record` · `methodology` · `delta-log` · `weights`
(per-user weight overrides) · `welcome` (first-run tutorial) · `login` (hosted-app auth) ·
`import` (Goodreads onboarding import) · `try` (public no-login prediction demo) ·
`directory` + `profile` + `u` — the `/u/[handle]` public-profile viewer (see below),
plus the `/` home. The former fiction/nonfiction route split was collapsed into a
single set of top-level pages — `tier-list`, `series`, `reading`, and `timeline` —
each with an in-page fiction/nonfiction type toggle that swaps the kind-parametrized
view components in `components/views/*View.tsx`. **Rankings was merged into `stats`**
(2026-07-31): `/stats` now leads with the summary dashboard (totals, tier distribution,
books-per-year), then a "Rankings" section that embeds `RankingsView` (the same
fiction/nonfiction/all toggle + full tables, including the cross-type leaderboard) via
its embedded prop. `/rankings` — and the old `/fiction|/nonfiction/rankings` — now
redirect to `/stats` (see `next.config.ts`), preserving `?type=`; `#rankings` jumps to
the tables, and the top-level "Stats" nav link occupies the slot Rankings used to hold.
The cross-type table ranks by **WA** (was Total Average); `StatsClient` renders its own
copy only when `showRanking` is set — the merged page passes false so the leaderboard
isn't duplicated, while the public-profile Stats tab keeps it. Nav lives in
`components/Nav.tsx`; API calls in `lib/api.ts` (static-mode via
`NEXT_PUBLIC_STATIC_DATA`); types in `lib/types.ts`; read-only gating in
`lib/readonly.ts`.

### Public profiles (opt-in cross-user browse)

The one place the app reads **across the tenant boundary on purpose**: a signed-in
viewer browses another reader's rankings / tier list / to-read queue / stats at
`/u/<handle>`, discovered via `/directory`. Opt-in and **private by default** —
claimed on `/profile`. Design (per the 2026-07-31 inspection): **app-layer only, no
RLS** (the backend holds one pooled superuser DSN, so RLS would be bypassed anyway;
tenant isolation is enforced by `WHERE user_id=?` everywhere). The identity/visibility
metadata lives in a new `profiles` table (`user_id` PK, unique `handle`, `is_public`;
self-healing migration + `db_write.set_profile`/`get_profile_by_handle`/
`get_profile_by_user`/`list_public_profiles`). The cross-user endpoints
(`GET /api/users/{handle}/{books,tiers,read-queue,stats}`, `GET /api/profiles/directory`,
`GET/PUT /api/profile/me`) are **thin delegations** to the existing tenant-scoped
handlers called with the *target's* `user_id` — so the viewer sees the owner's
rankings on the **owner's own weights**, and no prediction math is reimplemented.
The intentional cross-tenant hole is exactly `_resolve_public_target` → 404 for a
missing OR private handle (never confirms a private handle exists). Every route is
auth-gated on the **viewer** + has its own viewer-keyed rate-limit bucket
(`_RL_PROFILE`). The frontend reuses `RankingsView` / `TierListView` / the read-queue
clients / `StatsClient` (`RankingsView` and `StatsClient` now take default-off
`embedded` / `showRanking` props; the profile Stats tab's cross-type table sorts by
WA), forced read-only via `ReadOnlyProvider` +
`useReadOnly()` (`lib/readonly-context.tsx`) — a subtree override that ORs with the
global `READONLY`, so every existing (unwrapped) page stays byte-identical.
Regression guard: `test_public_profiles.py` (the gate) + `test_tenant_scope.py`.

## Working rhythm

- One feature per commit. After a change, verify the app still runs and the affected page
  works, confirm `test_engine.py` still passes cleanly (all checks PASS, no `[FAIL]` lines),
  then commit with a descriptive message.
- When in doubt about whether something is a derived-math change or a presentation change:
  if it changes a number, it's probably math (read-only); if it changes how an existing
  number is displayed or sorted, it's presentation (fair game).
- **Data lint:** `scripts/lint_data.py` runs inside `scripts/export_static_data.py` (both the
  full export and `--check`), so it gates every commit and push. ERROR findings — duplicate
  `(series, series_number)`, a read book left `done=0` in `recommendations`, a null/invalid
  genre — block the publish; WARN findings don't. Convention-dependent duplicates awaiting an
  owner decision are excused in `scripts/lint_allowlist.json` (remove an entry to restore the
  block).
- Pre-deploy: if `predict_engine.py` or `validate_engine.py` changed, regenerate the
  prediction-interval residual table (`python3 validate_engine.py --write-residuals`) so
  `calibration/residuals.json` matches the live engine (else served intervals show "stale").

## Walk-forward validation (backtest)

`walkforward.py` is a **chronological backtest**: for each rated fiction book it predicts what
the engine *would* have said on the day it was started, training on **only the books read
before it** (Timeline read order). Unlike `validate_engine.py`'s leave-one-out (which trains on
future books too), this is the honest *"what was knowable then"* accuracy baseline that future
engine features must beat, and the raw dataset for a future public track-record page. It
**calls the read-only engine unchanged** and never touches prediction math or `books.db`.

- **Run:** `python3 walkforward.py` (writes `validation/`), `--report-only` (rebuild the report
  from the folds artifact), `--check-determinism` (assert two runs are byte-identical),
  `--burn-in N` (min pool size before a fold is evaluated, default 15).
- **Zero API spend, structurally.** It reads the richer-prompt cache (`llm_scores_richer.json`)
  as a plain dict and blocks `anthropic.Anthropic`; a book with no usable cache entry is logged
  `SKIPPED_NO_CACHE`, never researched. There is no override flag.
- **Three variants per fold** (all from cache): **raw** (grounded research → WA, no
  correction), **honest** (author+genre correction fit on the *past-only* pool — the
  walk-forward baseline), **leaky** (correction fit on the *full library* = today's config).
  The **leaky** variant is labeled leaky everywhere because its correction saw future books —
  it answers "how good is today's config," not "what was knowable then." The retired,
  never-applied `component_corrections` (DeltaTracker) layer enters **no** variant. Refitting
  the correction per-fold on the pool (a fully-honest "variant 3") is future work.
- **Caveats:** research-cache vectors embed post-publication reception (accepted hindsight);
  the per-fold interval recorded is the engine's overconfident `±1.645·resid_sd` band, *not*
  the calibrated served conformal interval (the report scores that separately). See
  `validation/README.md`.
- **`validation/` artifacts don't churn on data edits** — every *book-data* snapshot file is
  derived from `books.db`, so editing ratings never restains these files. The Methodology
  page's engine-validation payload derives from these artifacts (not `books.db`); the
  per-user Track Record derives from each tenant's own `delta_log` and is unrelated to them.
- **Personal Track Record.** `frontend/app/track-record/` (page + `TrackRecordClient.tsx`) is
  fed by the **tenant-scoped** read-only `GET /api/track-record` endpoint (auth dep like every
  data route). For each caller it fetches their own `delta_log`, dedups to one authoritative
  row per genuinely-finished book via `delta_log_view.visible_rows` (Req1 finished-only + Req2
  live>backfill>retro), enriches missing mechanism-metadata (`corr_wa`/`n_author`/`pred_genre`)
  from other rows for the same title, and hands the deduped rows to
  `track_record.build_track_record`. Returns 404 (→ "not enough yet" empty state) when the
  reader has fewer than `track_record.MIN_TRACK_RECORD` (8) predicted+finished books.
  Zero-API, zero-engine, zero-writes — pure function of stored per-user data. Served-band
  coverage is computed via the canonical `intervals.interval_for(residuals, n_author)` per
  row, so it can never drift from what Predict/Read-queue actually serve. The retired
  `resid_sd` "old band" comparison is **removed** — the payload carries only
  `interval_coverage.served_conformal`; nothing else. Snapshotted deterministically as the
  default user (Michael) to `track-record.json` (`SIMPLE_ENDPOINTS`, `allow_404`); the
  provenance carries `data_source: "personal"` and `min_books` — no HEAD/timestamps.
  Fetch via `fetchTrackRecord()` (token threaded); Nav link under "More".
- **Engine validation (walk-forward on the reference library).** `engine_validation.py` reads
  the committed `validation/walkforward_*` artifacts and returns
  `{headline, served_coverage, provenance}` — the honest chronological accuracy of the engine
  on the reference library. Served by the unauthenticated `GET /api/engine-validation`
  (global, not tenant-scoped) and snapshotted to `engine-validation.json` (`allow_404`,
  deterministic per commit). Consumed only by the Methodology page. The retired legacy-band
  coverage was dropped from this payload too; served coverage is computed through
  `intervals.interval_for`. This is deliberately decoupled from `/api/track-record` (personal)
  so a change to one payload can't silently redefine the other.
- **Methodology page ("How the Engine Works").** `frontend/app/methodology/` (page +
  `MethodologyClient.tsx`) documents the engine *as it runs* — the 14-component weighted schema,
  empirical-Bayes shrinkage, the conformal 80% band, and walk-forward validation — in **two
  switchable tellings** (a default "Plain English" view and a "Technical" view; SubTabs-style pill
  toggle). It is fed by the read-only, **tenant-scoped** `GET /api/engine-parameters` endpoint
  (auth deps like every data route), which assembles a payload via `engine_parameters.py` from the
  **caller's** live engine: their effective schema + per-genre weights (overrides included), their
  library size and whether their calibration is own-fit or the borrowed seed
  (`library.model_source` / `min_own_fit`), their cold-start term (`cold_start.source` = `fitted`
  on their own residuals / `preference` from onboarding / `off`, plus the favorite-author-prior
  flag), their rating-scale anchors (`score_anchors` — the prose→number bands, read live off
  `score_anchors.BANDS`, with a `customized` flag; the Technical flow shows the remap as its own
  stage), and the served shrinkage / interval / model constants read straight off the modules that
  implement them (`reresearch_and_measure`, `research_predict`, `intervals`) — nothing is
  hardcoded. Both views branch their prose on those per-user fields, so the page is correct for
  every tenant, not just the seed. Math renders via **KaTeX** (the only frontend dep this added;
  client-side, static-safe). Snapshotted deterministically as the default user to
  `engine-parameters.json` (registered in `SIMPLE_ENDPOINTS`; no timestamps/HEAD in the payload).
  Fetch via `fetchEngineParameters()` (token threaded); validation baselines come from
  `engine-validation.json` (the reference-library walk-forward, served separately by
  `engine_validation.py`), NOT from the (now personal) track-record payload — the two are
  decoupled by design so a change to one can't silently redefine the other. The Methodology
  page cross-links to the personal Track Record for the reader's own predicted-vs-actual.
  Nav link under "More".
  - **This is the anti-drift design, and the main maintenance risk.** The page's *numbers* are read
    live, so a future engine change (a weight, a `K` constant, the served model, the interval level) is
    reflected automatically — but only if it stays reachable through this endpoint. The page's
    *concepts* are hand-written prose. So: when you change engine math, verify the new value surfaces in
    `/api/engine-parameters` (add it if it's a genuinely new parameter), and re-read only the prose for
    a **conceptual** change. Regression guard: this page must describe conformal intervals (never a
    `resid_sd` CI). The retired `component_corrections` (DeltaTracker) layer is **no longer mentioned
    on the page**, and the former "What it can't do" limitations section is gone (owner decision,
    2026-07-21 — the `correction` payload block was removed with them); the layer itself stays retired
    and unwired in the engine and must never be described as active anywhere.
