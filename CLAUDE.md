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
   plus the nonfiction equivalents). Do not add new write functions unless explicitly asked.

3. **DB schema is fixed.** The `books` table has `title, genre, author, series,
   series_number, words, year_read, status` plus the 14 component columns. `recommendations`
   has the same components plus `series_number, done, blurb, keywords`. Do **not** add columns
   without an explicit schema-change task that goes through `db_write`.

4. **`test_engine.py` must stay at a clean pass (every check PASSES, no `[FAIL]` lines —
   currently 29/29).** The DB is the source of truth; the Excel workbook is import-only, so
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

This app is **localhost single-user only — no auth of any kind.**

- All write/delete endpoints are intentionally unauthenticated. That is safe on loopback.
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

Top-level: `add-book` · `edit-ratings` · `predict` · `read-queue` (fiction) · `stats` ·
`analytics` · `calibration` · `track-record` · `delta-log`, plus the `/` home. Fiction and nonfiction otherwise
split into route groups: `fiction/{rankings, tier-list, series, reading, timeline}` and
`nonfiction/{rankings, tier-list, series, reading, timeline, read-queue}`, sharing view
components in `components/views/*View.tsx` (kind-param). Nav lives in `components/Nav.tsx`; API
calls in `lib/api.ts` (static-mode via `NEXT_PUBLIC_STATIC_DATA`); types in `lib/types.ts`;
read-only gating in `lib/readonly.ts`.

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
  derived from `books.db`, so editing ratings never restains these files. The one exception is
  the track-record page below, whose snapshot derives from these artifacts (not `books.db`).
- **Public track record.** `frontend/app/track-record/` (page + `TrackRecordClient.tsx`) is fed
  by the read-only `GET /api/track-record` endpoint, which assembles a payload from the
  committed `validation/` artifacts via `track_record.py` — predicted-vs-actual, the rolling-MAE
  "getting smarter" curve, MAE by genre, and served-interval coverage. It reads only committed
  files (**never runs the harness**, no `books.db`, no API spend) and computes served coverage
  through the canonical `intervals` module, so nothing drifts. It shows the **honest** variant
  (leaky excluded). Snapshotted deterministically to `track-record.json` (registered in
  `SIMPLE_ENDPOINTS`, `allow_404`); it only changes when the harness output is regenerated and
  committed. Fetch via `fetchTrackRecord()`; Nav link lives under "More".
