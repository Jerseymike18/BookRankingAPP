# CLAUDE.md — The Reading Ledger

Standing instructions for working in this repository. Read before making any change.

## What this project is

A personal book-tracking and prediction web app. The owner rates every book they read
across 14 fine-grained components, and the app predicts how much they'll enjoy any future
book before reading it. It is a precision instrument calibrated to one person's taste — not
a discovery service, not multi-user, not deployed (runs on localhost).

## Stack

- **Backend API:** FastAPI (Python), uvicorn, SQLite via `books.db`
- **Frontend:** Next.js (App Router), Tailwind CSS v4
- **LLM:** Anthropic Claude via `apikey.txt` (untracked — see Secrets)
- **Data engine:** pure Python (`db_loader.py`, `predict_engine.py`, `views.py`, `db_write.py`)
- **Launch:** `bash start.sh` from project root (starts both servers)
- **URLs:** Frontend http://localhost:3000 · API http://localhost:8000

## HARD CONSTRAINTS — do not violate

1. **Never touch prediction/derived math.** `predict_engine.py`, `db_loader.py`, and
   `views.py` are read-only reference implementations. The frontend and endpoints call them;
   they never reimplement or duplicate their logic. WA computation, tier banding, and series
   Adjusted WA all live here and stay here.

2. **All writes go through `db_write.py`.** Never write direct SQL to the database from the
   backend or anywhere else. Use the existing validated functions only:
   `add_book`, `change_rating`, `delete_book`, `set_year_read`, `set_status`,
   `update_queue`, `add_recommendation`, `set_recommendation_meta`.
   Do not add new write functions unless explicitly asked.

3. **DB schema is fixed.** The `books` table has `title, genre, author, series, words,
   year_read, status` plus the 14 component columns. `recommendations` has the same
   components plus `done, blurb, keywords`. Do **not** add columns without an explicit
   schema-change task that goes through `db_write`.

4. **`test_engine.py` must stay at a clean pass (9/9, no `[FAIL]` lines).** The DB is the
   source of truth; the Excel workbook is import-only, so Excel/DB drift is expected and is
   printed as informational (not pass/fail). Any `[FAIL]` line means something broke —
   investigate before proceeding.

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

`rankings` · `tier-list` · `series` · `reading` · `timeline` · `predict` · `read-queue` ·
`add-book` · `edit-ratings`. Nav lives in `components/Nav.tsx`. API calls in `lib/api.ts`,
types in `lib/types.ts`.

## Working rhythm

- One feature per commit. After a change, verify the app still runs and the affected page
  works, confirm `test_engine.py` still passes cleanly (9/9, no `[FAIL]` lines), then commit
  with a descriptive message.
- When in doubt about whether something is a derived-math change or a presentation change:
  if it changes a number, it's probably math (read-only); if it changes how an existing
  number is displayed or sorted, it's presentation (fair game).
- Pre-deploy: if `predict_engine.py` or `validate_engine.py` changed, regenerate the
  prediction-interval residual table (`python3 validate_engine.py --write-residuals`) so
  `calibration/residuals.json` matches the live engine (else served intervals show "stale").
