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
   Adjusted WA all live here and stay here. The **series-quality model** is the one piece
   of this that was deliberately reworked (owner decision, 2026-08-16): it is OWNED by
   `views.py` and belongs there, but it is no longer a frozen port of the spreadsheet
   formula. Change it only on an explicit request, and re-run `test_series_score.py`.
   `db_loader` also now passes `series_number` through as a `"Series #"` column — a
   read-only passthrough like `year_read`/`status`, which the series model needs to
   order a series and find its final volume.

2. **All writes go through `db_write.py`.** Never write direct SQL to the database from the
   backend or anywhere else. Use the existing validated functions only (e.g.
   `add_book`, `change_rating`, `delete_book`, `set_year_read`, `set_status`,
   `update_queue`, `add_recommendation`, `set_recommendation_meta`,
   `update_recommendation_scores`, `update_book_metadata`, `set_series_number`, `set_done`,
   `set_score_anchors`, `reset_score_anchors`, `set_series_complete`, plus the nonfiction equivalents —
   including `update_nonfiction_recommendation_scores`, the in-place nonfiction score
   writer added 2026-08-14). Do not add new write functions unless explicitly asked.

3. **DB schema is fixed.** The `books` table has `title, genre, author, series,
   series_number, words, year_read, status` plus the 14 component columns. `recommendations`
   has the same components plus `series_number, done, blurb, keywords`. Do **not** add columns
   without an explicit schema-change task that goes through `db_write`.
   - **`series_meta`** (added 2026-08-16 with owner authorization) is the one place a fact
     about a series AS A WHOLE can live — a series is only a grouping of `books` rows by the
     free-text `series` column, so there is no row to hang it on. Sparse per-tenant
     (`user_id, series, complete`), self-healing via `db_write._ensure_series_meta`, written
     only through `db_write.set_series_complete`. **No row means not complete** — which now
     suppresses the Finale term *and* applies the small Unfinished charge, so the flag is
     load-bearing: an unmarked-but-actually-finished series is scored as ongoing. (A caller
     that supplies no `series_meta` at all is a different case and charges nothing — see the
     tri-state note under the series score.) Unmarking deletes the row rather than storing a zero.

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

**Granular (one-book, on-demand) re-prediction.** `repredict_on_add.repredict_one` is the
deliberate opposite of the cohort pass: the reader points at ONE unread recommendation and
re-predicts just that book against the library as it stands now — no cohort, no gate, nothing
else in the TBR touched. Served **synchronously** by `POST /api/recommendations/{title}/repredict`
(auth-gated, `_RL_LLM` bucket, fiction only), driven from a per-book **Re-predict** button in the
read-queue's expanded panel. It runs the FULL live path (research an uncached book, make the
deferred web call), so it is slow on a never-grounded book and instant on a warm one — and it
doubles as the manual repair for a rec saved memory-only that never got its background grounding
upgrade. Three things are load-bearing:

- **One prediction core.** `repredict_one` and `ground_saved_rec` both run `_predict_rec`, so the
  two single-book paths can never drift into predicting the same book two different ways.
- **The audit tag keeps its prefix.** Rows are tagged `baseline_repredict:manual:<title>`; that
  `baseline_repredict:` prefix is what `delta_log_view` filters out of the Delta Log, so a manual
  re-prediction can never be mistaken for a genuine predicted-vs-actual delta once the book is
  read. Never retag these rows without updating `delta_log_view._REPREDICT_PREFIX`.
- **The cold-start term IS applied to the report — to both sides, on the read-queue's gate.**
  The report's WA/rank must equal what the read-queue and Predict page show for the same book, or
  the reader sees two numbers for one row. Two rules keep that honest: it is applied to **both**
  old and new (the adjustment is a function of the book's metadata, not of WA, so both sides shift
  equally and ΔWA is untouched — except for a book pinned at the 0/10 clamp), and its gate uses
  the **`rank_pool` (own-library) counts**, the same rule as `_cold_adjust_rec_wa`, *not*
  `correct_and_predict`'s internal correction-pool counts. `new_rank` is likewise recomputed on
  the display WA rather than taken from `correct_and_predict`. `rank_pool` is the reader's own
  library (never the borrowed seed pool) — the cold-start rank-leak guard.
  *(Fixed 2026-08-14; before this the report omitted the term and disagreed with the row it had
  just rewritten by up to ~0.18 WA on cold-slice books. Gate: the read-queue-agreement checks in
  `test_repredict_one.py`.)*

**Nonfiction granular re-prediction is a DIFFERENT operation — don't unify them.**
`repredict_nonfiction_one` + `POST /api/nonfiction/recommendations/{title}/repredict` exist, but
nonfiction has **no correction layer** (research vector → anchors → weighted roll-up, that's all),
so a nonfiction book's stored scores don't depend on the reader's library and there is no baseline
to move. Re-running the cached path would return a byte-identical vector forever. So the
nonfiction path **forces a fresh research call** (`force=True`) and therefore **always spends one
Opus call** — there is no cheap path, and the UI gates it behind a confirm step for that reason
(owner decision, 2026-08-14). Three further divergences that must not be "fixed" into parity:

- **No `delta_log` row.** `delta_log` is fiction-shaped (its pred_/act_/d_ columns are the 14
  `FICTION_COMPONENTS`). The move is reported, not recorded; writing one would be a schema change.
- **No interval** — nonfiction has no residual table, and the guard forbids a variance substitute.
- **Rank is recomputed by WA**, not taken from `research_and_predict`'s own `rank` field, which is
  by Total Average — reusing it would report an old→new rank move measured on two scales.

Writes go through `db_write.update_nonfiction_recommendation_scores` (added 2026-08-14 with owner
authorization — the mirror of the fiction function; nonfiction previously had no in-place score
writer at all).

**Measured run-to-run variance — REDONE 2026-08-15 on the refreshed library.** The first pass ran
against a stale `books.db` (nonfiction rows carrying half their components, and ZERO nonfiction TBR
entries, so the test book had to be staged). After `scripts/refresh_from_live.py`, local reproduces
the owner's live nonfiction rankings exactly. Redone on **two real TBR books, n=6 forced calls
each**, zero library change:

| book | WA sd | range | rank across 6 runs | components that varied |
|---|---|---|---|---|
| Outliers (Gladwell) | 0.030 | 0.075 | #6 — no flip | 5 / 12 |
| SPQR (Beard) | 0.034 | 0.075 | #3 — no flip | 2 / 12 |

Live library: WA 5.24–8.49, gaps `[0.36, 0.30, 0.27, 0.55, 1.78]`, median **0.362**; served
directional band **±1.333** (n=6).

- **Noise is ~2.3–2.5% of the served band**, and `range ÷ median gap` ≈ **0.21** — so roughly a
  1-in-5 chance a typically-placed book shifts one rank. **Rank did not move at all in 12 of 12
  samples.**
- Every earlier nonfiction figure here was inflated by the stale copy: sd 0.052 (not ~0.030), range
  0.159 (not 0.075), band ±1.67 (not ±1.33 — the partial vectors distorted the LOO residuals), and
  a claimed ~40% flip rate (not ~21%). The production app always used the correct band; only the
  local reading was wrong.
- The book that produced those numbers, *Beyond Good and Evil*, remains a legitimate third data
  point (sd 0.052) — the research call never depended on the library. So nonfiction noise IS mildly
  book-dependent, 0.030–0.052, about 1.7x — far less than fiction's 4x.
- **The "contested reception" hypothesis does NOT replicate here.** Outliers (contested pop-sci) was
  the *quietest* (0.030) and SPQR (settled history) marginally noisier (0.034), while contested
  *Beyond Good and Evil* was the loudest. Mixed at n=3 books; treat the fiction pattern as unexplained
  rather than established. Note also that component churn ≠ WA noise: Outliers moved 5 of 12
  components yet ended with the lower WA sd, because the weighted roll-up cancels offsetting moves.

So the nonfiction button is quieter and more rank-stable than any earlier statement in this file
claimed. Don't re-litigate without a bigger library.

**Measured fiction run-to-run variance (2026-08-14, n=6 forced calls each on two books, zero
library change).** Unlike nonfiction's, fiction's noise floor is **not one number — it is
book-dependent, spanning ~4x**:

| book | n_author | WA sd | range | % of served band | rank spread |
|---|---|---|---|---|---|
| Crime and Punishment | 2 | 0.026 | 0.072 | 3% | 3 places (17–20) |
| The Silmarillion | 4 | 0.112 | 0.327 | 13% | 13 places (79–92) |

**WA is stable; RANK is fragile.** The noise is small against the served conformal band (3–13%) and
against the engine's honest walk-forward MAE (0.628) — so the WA estimate is trustworthy. Rank is
not; the live-library figures below quantify it. Do not read a rank move from a re-predict as
signal. (Nonfiction is fragile in the opposite way: its noise is *larger* relative to its band, but
its gaps are wider than its noise, so only near-boundary books move.)

Plausible mechanism, NOT established at n=2 books: variance tracks how contested a book's reception
is. The Silmarillion's biggest movers were Action (sd 0.31), Plot (0.23), Emotional Impact (0.23) —
exactly what readers disagree about — while Crime and Punishment's reception is settled.

**REDONE against the live library (owner-supplied WA column, 2026-08-14): 140 books, WA 3.78–9.70,
median adjacent gap 0.0200**, and 23 of 139 adjacent pairs are ties at 2dp (separated by <0.005, so
they reorder on any noise at all). The earlier local-`books.db` figure (131 books, median 0.0213)
projected to 0.0199 and was right — the median is set by the dense middle, and the two libraries
differ mainly in the sparse tail (local span 7.46 vs live 5.92).

**What that means per book, which is more useful than the ratio:**

| noise range | books with ≥1 neighbour inside it | places a re-predict can reorder (median / max) |
|---|---|---|
| 0.072 (quiet book) | 114 / 140 — **81%** | 2 / 7 |
| 0.327 (contested book) | 136 / 140 — **97%** | 12 / 23 |

So for fiction, a re-predict reshuffles the book by ~2 places at best and ~12 typically for a
contested one, and essentially the whole library is exposed. Compare nonfiction, where the gaps
(median 0.36) are wider than the noise (0.159) and only near-boundary books move.

**The one number across both tracks: noise range ÷ local WA gap** ≈ the chance a re-predict moves
the rank. Nonfiction ≈ 0.159/0.36 ≈ 40% (a one-place flip is common). Fiction ≈ 0.072–0.327/0.020 =
**3.6× to 16.3×** — saturated, so rank moves on essentially every press, by several places. This is
why the standing guidance is act on WA, ignore rank movement from a re-predict.

Two mechanics worth knowing before re-measuring: `force` bypasses only the **memory** research
cache, so the 6 web-grounded components (Depth, Depth2, Ending, Insights, Integration, Originality)
are NOT re-sourced; but they still move in the output, because `smooth_components` predicts each
component from the other 13, propagating memory-side noise into all 14. Measure the served output,
not the raw vector.

**Predict page (fiction only).** The same button also sits on each Predict card, where it means
the **no-cache refresh** (`ResearchRequest.force`) — distinct from **Refine**, which upgrades
memory scores to grounded ones and is offered only until a book IS grounded. Cards are candidates,
so it normally writes nothing; but for a book already **saved**, the client follows with the
re-predict endpoint so the card and the stored row can't disagree. That second call is free: the
forced call already overwrote the book's research-cache entry, so the cache-first endpoint persists
exactly the vector on screen. Config-gated via `PredictFlowConfig.repredict`, left undefined for
nonfiction (whose re-predict lives on its read-queue, where it can persist).

Regression guard: `test_repredict_one.py` (40 checks — scope, tenant isolation, eligibility, the
tag, the no-op guard, both-paths-agree, the endpoint's `rank_pool` wiring, report/read-queue WA +
rank agreement under an active cold-start term, and for nonfiction: that
the fresh-research force actually bypasses a warm cache, that no `delta_log` row is written, and
that the reported WA/rank agree with the read-queue). A dry-run CLI is
`python3 repredict_on_add.py "<title>" --one --dry-run`.

## Genre recommendation — "what should I read more of" (`genre_affinity.py`)

Predict answers "how much will I like THIS book". Discover answers "find me books
matching this request". Neither answered the question that comes first: **what should
the request be.** Discover's generator saw the reader's titles (to avoid them) and their
genre list (to copy spellings from) and **not one number about how they actually rate
genres** — genre choice was whatever they typed. `genre_affinity.py` is that missing
evidence, served by `POST /api/discover/genres` and driven from **`/predict/genres`
(Genre Prediction)** — its own page under the Predict nav group, beside `/predict`
(Book Prediction). See **Pages** for why they are separate pages.

**Genre Prediction is fiction-only, and structurally so** — the page has no
fiction/nonfiction toggle at all, so it can never promise a nonfiction answer. (Nonfiction
has one `nonfiction_genre_weights` row and 6 rated books; there is no per-genre evidence to
argue from, and an empty recommender is worse than none.) Two consequences of it being a
separate page, both load-bearing:

- **Its state lives in the JOB PROVIDER** (`predict-jobs.GenreTabState`), not the page.
  "Recommend → hand a request to Book Prediction → come back" is the normal path and is now
  a real navigation, so page-local state would discard a recommendation it had just spent a
  call to produce. The provider is mounted above the router in the root layout, which is the
  same reason `activeKind` lives there. It **is** mirrored to the tab snapshot (owner
  request, 2026-08-24), so a recommendation also survives a reload. Four rules keep that
  safe, and each is easy to undo by accident:
  - It rides the **same `STORAGE_KEY` as the runs**, so `clearPredictJobs` already wipes it
    on sign-out. That is required, not incidental — this is derived wholly from one reader's
    library, and a shared browser must never hand it to whoever signs in next.
  - `clearPredictJobs` also **latches persistence off** (`signedOut`). Sign-out awaits the
    Supabase call before navigating, and anything landing inside that window would setState →
    fire the mirror effect → rewrite the snapshot that was just deleted.
  - **`loading` and `error` are not persisted.** A reload kills the in-flight fetch, so
    restoring `loading: true` leaves a spinner nothing will finish; and an error describes a
    moment the reload already ended (the same reasoning that clears the Discover error banner
    on arrival). A snapshot taken mid-call restores `interrupted` instead, which says so.
  - The stored `result` is **shape-checked on restore** (`_isGenreResult`), not just
    null-checked — a corrupt or stale-shaped blob would otherwise reach `result.genres.map`
    and take the page down on load, recoverable only by clearing storage. `STORAGE_KEY` did
    **not** need a version bump: `fromSnapshot` treats a missing `genreTab` as empty, and
    bumping it would have discarded the in-flight runs of anyone mid-scoring at deploy.
- **"Find books like this" hands off through the provider, not a query param.** It sets the
  fiction request, forces `activeKind` to fiction (landing on the nonfiction flow holding a
  fiction request is a dead end), flags `requestFocusOnArrival`, then pushes `/predict`.
  Book Prediction consumes that flag ONCE on mount and focuses the request box — without it
  the reader arrives at a box that filled itself silently, which reads as nothing having
  happened. A fiction run in flight disables the hand-off buttons **only**: asking for a
  recommendation is read-only and never conflicts with a run, but overwriting the request
  that run is using would.

**The module is READ-ONLY over the engine** — same standing as `track_record.py`,
`intervals.py` and `delta_log_view.py`. It computes no prediction, writes nothing, and
touches no scoring math. HARD CONSTRAINT 1 is untouched.

**Two halves, and the split is the design.** `genre_evidence()` is pure, deterministic
and zero-API; `recommend_genres()` is ONE `DISCOVER_MODEL` (Sonnet) call that narrates
those numbers. **Every figure in the response is copied from the evidence dict, never
from the model's prose** — the endpoint attaches `affinity`/`band`/`surprise` to each
pick itself. The brief the model saw is returned as `brief` so the argument is auditable.

Four things are load-bearing:

- **Affinity is a SHRUNK mean with a band, never a raw mean.** The naive ranking is
  noise: on the reference library it puts Russian Literature (n=3) and Gothic Fiction
  (n=2) above Epic Fantasy (n=59). Empirical-Bayes shrinkage toward the library mean,
  with the constant `k` fitted from the library's own between/within-genre spread
  (DerSimonian-Laird, floored so `k ≤ MAX_K`), plus an 80% band that widens as evidence
  thins. Thin genres keep their high estimate and *look* thin — nothing hidden, nothing
  invented, the same discipline as the omitted conformal interval. **The band is on a
  GENRE's mean rating and must never be presented as the served conformal interval**,
  which is on an unread book's predicted WA.
- **Signed surprise, from ENGINE-produced forecasts only** (`engine_forecast_rows`).
  `track_record` reports |error| by genre — how *reliable* the engine is there. The
  recommender needs the *direction*: a genre the engine systematically under-predicts is
  one the reader enjoys more than the model expects, and that is the most actionable
  thing in the payload. It deliberately uses a **different row set than the Track
  Record**: `delta_log_view.visible_rows` prefers live > workbook-backfill > retro_sweep,
  which is right for "what did this reader forecast at the time" but wrong here — the
  backfill predates the engine and is coarse (all four Literary Fiction books carry the
  identical spreadsheet-era `pred_wa` 6.0245). Measured on the live library, including
  it flattens the spread from **1.72 WA to 0.49** and reports Literary Fiction at −0.03
  instead of **−1.16**. So the backfill rows are dropped and `visible_rows` is then run
  over the rest (Req-1 filtering stays owned by `delta_log_view` — never duplicated),
  leaving live > retro, both engine-produced. A reader with no backfill rows (everyone
  but the seed) is unaffected.
- **The worldbuilding mask.** Worldbuilding is scored 0 for realist genres, so an
  unmasked component z-profile reports Literary Fiction at **−2.26** on Depth2 /
  Integration / Originality — which reads as "this reader hates its worldbuilding" when
  it means "there is none to score". Those three are `None` for any genre whose
  Worldbuilding weight is 0 (falling back to the data when no weights are supplied).
- **Volume is not affinity.** The dominant genre wins any raw ranking by weight of
  numbers (Epic Fantasy is 42% of the reference library). Read share is reported
  *separately* from affinity, and the prompt forbids recommending the most-read genre
  merely for being biggest.

Two guards on what the LLM may say, both failing closed:

- **A recommended genre outside `genre_weights` is dropped.** It has no weights row, and
  every WA roll-up reads weights defensively — so it would score a confident-looking
  **0.00** downstream. Same hazard `test_genre_guard.py` exists for.
- **A "type" stating a decimal is dropped.** The `types` half names a mode or tradition
  the 16-genre schema has no label for ("secondary-world fantasy that sticks its
  ending") and by construction has **no data behind it**; a decimal there is an invented
  score. Types are rendered as hypotheses, never measurements.

`MIN_LIBRARY_BOOKS = 15` refuses the whole feature below that — deliberately the same
threshold the cold-start model uses to decide a tenant can stop borrowing the seed, so
the app has one answer to "is this library big enough to speak for itself".

Each pick carries a `discover_request` string that drops into the Discover box, so
"read more Gothic" reaches actual scored books through the pipeline that already
exists — no new scoring path. Regression guard: **`test_genre_affinity.py` (46 checks)**
— shrinkage/band behaviour, the worldbuilding mask, the surprise row-selection (including
an explicit check that the backfill *would* flatten it), both LLM guards, and the
endpoint's auth/rate-limit/tenant-scope/read-only/no-write wiring. Zero-API: the library
is a fixture and the client is a stub. CLI: `python3 genre_affinity.py [--brief]`.

## Serving latency — what is allowed to be stale, and what is not

Three costs dominated page latency before 2026-08-20; the fixes are load-bearing enough
that undoing one silently re-slows the whole site.

- **The cold-start term is STALE-WHILE-REVALIDATE, on purpose (owner decision,
  2026-08-20).** `_fit_cold_term_for` is a leave-one-out pass over the entire library
  (~140 `correct_and_predict` calls, ~2s on the seed), and five endpoints need it
  (`/api/read-queue`, `/api/reading/stats`, `/api/predict/research`, `/api/delta-log`,
  `/api/recommendations/{title}/repredict`). It used to be **evicted by every write** and
  refitted synchronously by the next reader — and because the backend is a single
  GIL-bound process, that stalled *every concurrent request*, not just the one that
  needed the term. Measured: the read-queue page cost 100ms warm and **2015ms after any
  write**; it is now **324ms**.
  `backend/main.py` now keys `_cold_term_cache` on the `_engine_epoch` the term was
  fitted at and serves the previous fit while a refit runs on its own single-thread
  executor. **So for a second or two after a write, the served term is the one fitted on
  the library as it stood one write ago** — an OLS slope over the whole library, which a
  single book moves by a hair. Three properties keep that honest and must survive any
  rework: a tenant with **no** previous fit is still fitted synchronously (nothing is
  invented — same rule as the omitted conformal interval); a **failed** refit never
  clobbers the last good value; and a legitimate `None` fit ("too few books") is cached
  as None, so absence and None stay distinguishable. Regression guard:
  `test_cold_term_cache.py` (11 checks). Do NOT reintroduce a
  `_cold_term_cache.pop(...)` into `_invalidate_engine` — the epoch bump already marks
  the term stale.
- **`/api/read-queue?blurbs=0` drops the blurb paragraphs** (221 KB of 262 KB of prose on
  this TBR; 205 KB → 119 KB gzipped on the wire, paid twice — Railway→Vercel and
  Vercel→browser). Only the read-queue *page* passes it; the default keeps blurbs inline,
  so the static export and the public-profile delegation are unchanged. The card
  lazy-loads one blurb on expand via `GET /api/recommendations/{title}/blurb`, keyed on
  `blurb === undefined` — **not** on a mode flag, because the public-profile view renders
  the same component against another reader's rows and must never fetch the viewer's own.
  `keywords` is NOT droppable: the page's keyword filter searches it client-side across
  the whole list.
- **`proxy.ts` verifies the JWT locally (`getClaims`), not over the network
  (`getUser`).** The proxy runs before the render on every navigation, so a round trip
  there is added to every tab switch; this project signs with ES256, the asymmetric case
  `getClaims` verifies against a cached JWKS. The trade is that `claims.user_metadata` is
  as of token issue — which is why the welcome wizard calls `refreshSession()` right
  after `updateUser()` (`updateUser` does **not** mint a new token, so without it the
  reader would be bounced straight back to `/welcome`, and their stated preferences would
  sit unused for up to a token lifetime). The backend already reads `user_metadata` from
  the same claims, so the two now agree.

### The database is FAR, so round trips are the unit of latency

Measured 2026-08-21 via `GET /health?db=1` (an opt-in probe on the live backend — opt-in
because making the platform's health check depend on Postgres would turn a DB blip into a
container restart):

| | one round trip to Supabase |
|---|---|
| a laptop on residential broadband | **24 ms** |
| **Railway** | **63 ms** |

So latency here is not CPU — the engine rebuild is ~18 ms — it is **how many round trips a
request makes**. Optimise the count, not the code. Three things follow, and each is easy to
undo by accident:

- **`db_backend`'s pool `minconn` must never be 0.** psycopg2's `putconn` keeps a returned
  connection only `while len(pool) < minconn`, so `minconn=0` CLOSES every connection and
  the pool pools nothing — every `connect()` becomes a fresh TCP+TLS+auth handshake
  (measured 232 ms vs 73 ms). It shipped that way and the module's comment claimed
  otherwise; `connect_ms` on the probe is how you tell (0.0 = warm).
- **Reads run in autocommit** — `db_backend.readonly()` around a block, or
  `connect(..., readonly=True)`. A pooled connection comes back rolled back, so the next
  borrower's first statement must open a transaction: rollback + BEGIN + query. For a read
  that protects nothing. Verified in production: **127 ms → 63 ms per query, 2×.**
  It is SCOPED, and must stay scoped: blanket autocommit would break the multi-statement
  writers (`update_queue` rewrites the whole queue; the metadata writers cascade across
  tables), where each statement would commit alone and a mid-way failure would leave a
  half-applied change. It is thread-local because FastAPI serves sync handlers on a
  threadpool, and it is always cleared before the connection returns to the pool. The scope
  is entered in the CALLER so `db_loader.py` stays untouched. Guard:
  `test_multiworker.py`'s four readonly checks.
- **The Supabase SESSION pooler caps total clients at 15**, and refuses rather than queues —
  at the cap every request fails. `db_backend`'s connection budget derives the pool size
  from `DB_MAX_CLIENTS`, budgeting a container to HALF the cap so a redeploy (Railway runs
  the old and new containers together) still fits.
- **Query traffic goes to the TRANSACTION pooler (`:6543`), session traffic to the SESSION
  pooler (`:5432`)** — split by what each caller needs, not by preference. In transaction
  mode a server connection is assigned per TRANSACTION and returned immediately, so an idle
  client costs nothing, far more clients fit, and excess demand QUEUES rather than being
  refused; that is what lifted the pool from 2 to 6 per worker. But session state does not
  survive between transactions, so two callers must stay on `:5432`: `cache_sync`'s listener
  (it holds an open LISTEN for the process's life) and `db_write.CrossProcessLock`
  (`pg_advisory_lock` is session-scoped and held across statements). **The failure mode is
  silent** — transaction mode ACCEPTS a `LISTEN` and simply never delivers — so both use
  `db_backend.session_dsn()` explicitly. `DATABASE_URL` stays the session url; the query url
  is `DATABASE_URL_TX`, else the same url port-swapped. `DB_TX_POOLER=0` reverts everything
  to the session pooler. `GET /health?db=1` reports `pool.query_pooler`; if that ever reads
  `session`, the split has fallen back and the query pool is sharing the 15-client cap
  again. Verified against the live DB that multi-statement writes stay ATOMIC through
  transaction pooling (pgbouncer pins the server for the duration of a transaction) —
  guard: the four split checks in `test_multiworker.py`.

**Also removed from the write path, and worth not reintroducing:** `change_rating` /
`add_book` called `_show_computed_wa`, which ran a FULL library load (own connection, full
scan, whole pandas build) to print one line into a `StringIO` the API then discarded — it is
now TTY-gated. And `_backup_once` copied a 1 MB file on Postgres, where `DB` names the stale
`books.db` baked into the container image; it is now SQLite-only.

### Multi-worker: `--workers ${LEDGER_WORKERS:-2}` and the state it needed shared

The backend runs **two uvicorn worker processes**, pinned in the Procfile. Two details
there are load-bearing:

- **The count is `LEDGER_WORKERS`, not `WEB_CONCURRENCY`.** Railway sets
  `WEB_CONCURRENCY` itself (observed **3** on 2026-08-20), so reading it handed the live
  worker count to the hosting platform, where it would move silently if the box were
  resized. The Procfile overrides it. Tune with `LEDGER_WORKERS` — a name no platform
  sets — and never by setting `WEB_CONCURRENCY` in the environment, which the Procfile
  overwrites anyway.
- **`WEB_CONCURRENCY` is then exported from that same value**, because `backend/main.py`
  and `db_backend.py` read it to divide per-process budgets. If the number uvicorn forks
  on and the number the app divides by disagreed, those budgets would silently be several
  times too generous and the Postgres connection ceiling would be wrong.

Count the live workers without dashboard access: each holds exactly one `cache_sync`
listener, so `SELECT count(*) FROM pg_stat_activity WHERE query ILIKE '%LISTEN
ledger_cache%'` is the answer (a closed client listener leaves no parked session behind
the Supabase session pooler, so your own probes don't inflate it).

Everything below existed because module globals are per PROCESS.

- **Engine-cache invalidation is the load-bearing one.** `_invalidate_engine` bumps an
  in-memory epoch, and the engine/cold-term/correction caches have **no TTL** — so under
  workers, a write handled by worker A left worker B serving the **pre-write library
  forever**. `cache_sync.py` fixes it: a write calls `publish(scope, user_id)`, which
  atomically increments a shared epoch row and issues a Postgres **NOTIFY**; every worker
  holds one dedicated listening connection and drops that tenant's caches on receipt, plus
  re-reads all epochs every 10s as a **reconciliation** safety net. Both halves are needed
  — NOTIFY can be missed across a reconnect or a redeploy, and polling alone would leave a
  window where a worker serves the pre-write library right after an add.
  - `_invalidate_engine_local` / `_invalidate_nf_engine_local` are the local-only halves.
    **cache_sync's callback must call those, never the publishing ones**, or two workers
    notify each other in a loop.
  - The listener connection is deliberately **not** pooled (a session-long LISTEN is the
    opposite of a borrow-and-return connection), and **Supabase must stay on the SESSION
    pooler** — transaction-mode pgbouncer does not carry LISTEN/NOTIFY. If it ever moved,
    the reconciliation sweep silently becomes the whole mechanism (correct, just slower).
- **Re-prediction reports** move to the shared `app_state` table, keyed by token **and
  tenant** (a token alone would let a guessed token read someone else's report). The POST
  that starts the work and the poll that collects it land on different workers otherwise.
- **Rate limits: only the buckets that gate MONEY are shared** (`llm`, `demo_live`,
  `demo_live_global`, `signup`) — each fronts a paid Anthropic call, so N workers each
  honouring the full budget would mean N× the spend. They go through
  `db_write.rate_limit_try`, whose count-and-insert is one statement; under concurrency it
  can overshoot by at most the number of in-flight requests (~51 on a 50/day cap, never a
  multiple) — stated, not hidden. Every other bucket stays in memory with its budget
  **divided by the worker count** (`_local_budget`), because a DB write per list request
  would spend exactly the latency this release recovered.
- **`_repred_lock` is a `db_write.CrossProcessLock`** — a local `threading.Lock` plus a
  Postgres session advisory lock, on a dedicated connection (an advisory lock outlives a
  transaction, so leaving one on a pooled connection would hand the next borrower a lock it
  never took). Not re-entrant, matching the lock it replaced.
- **Two other per-process budgets are divided by the worker count** for the same reason:
  the background-grounding executor width (the 2026-07-21 A/B found grounded calls
  self-throttle past ~5–6 concurrent) and `db_backend.DB_POOL_MAX` (N×10 session
  connections plus each worker's listener and lock connection can exhaust the Supabase
  pooler — which fails as a connect error on every request, not as a slowdown).

**Local dev is untouched, on purpose.** `cache_sync.enabled()` is false on SQLite, and
`_shared_state()` gates every write to the new tables on it. That is not just an
optimisation: on local dev the database **is the tracked `books.db`**, so a rate-limit row
per LLM call would churn the file the autopublish watcher commits and the snapshot is
built from. One process has nothing to share, so it takes the in-memory path exactly as
before.

Two new **operational** tables back all of this (`app_state`, `rate_limit_hits` — HARD
CONSTRAINT 3 authorization, owner, 2026-08-20). Neither holds reader data or anything
derived from the scoring model; losing both costs one cache rebuild, one uncollected
report, and one rate-limit window. Regression guard: `test_multiworker.py` (20 checks).

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

**`books.db` drifts from the live app — refresh it before any claim about the data.** The hosted
Postgres is the sole source of truth (owner decision 2026-07-12) and nothing syncs automatically,
so the committed `books.db` goes stale from the moment the owner edits anything on the site.

    python3 scripts/refresh_from_live.py            # dry run: show the drift
    python3 scripts/refresh_from_live.py --write    # pull it down (backs up first)

**Standing practice (owner decision 2026-08-15): run the dry run before analysing the owner's
library, and `--write` before making any claim about what they have rated, own, or are missing.**
The file is fine unrefreshed for exercising CODE — engine behaviour, endpoint wiring,
throwaway-copy tests — because those don't depend on which books are in it. It is worthless
unrefreshed for claims about the data. If the refresh can't run, say the claim is unchecked rather
than reading the file anyway.

Why this is a rule and not a nicety: on 2026-08-14 the stale copy produced two confident and wrong
conclusions in one session — that the 6 nonfiction books were missing half their components (they
were fully rated live) and that no UI existed to enter them (`RankingsView` has been
kind-parametrized all along). The first refresh, on 2026-08-15, pulled +9 books, +32 recommendations,
+45 nonfiction recommendations and +66 delta_log rows, and surfaced two data ERRORs the lint had
never seen because the rows weren't in the local file.

**Middleware order is load-bearing.** `_cors_safe_errors` MUST stay registered *before*
`CORSMiddleware` in `backend/main.py` (`add_middleware` builds the stack so the last registered is
outermost). Starlette's own `ServerErrorMiddleware` sits outside all user middleware, so without
that net an unhandled 500 skips CORS, goes out with no `Access-Control-Allow-Origin`, and the
browser reports it as `TypeError: Failed to fetch` — no status, no message, on every endpoint.
Guarded by the CORS checks in `test_repredict_one.py`. (Note the same string also appears, for an
unrelated reason, when a request lands during a Railway redeploy.)

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
- **Series score (`views.series_quality_terms` / `series_aggregate`):**
  `avg_WA + clamp(Consistency + Peak + Finale + Unfinished, ±0.75) + Evidence`. Each prices
  something a mean over books is structurally blind to (a mean is order- and
  spread-invariant).
  - **NO LENGTH TERM — do not reintroduce one** (owner decision, 2026-08-17). The model
    used to carry a compounding `Commitment` bonus for long series. The owner **finishes
    every series they start**, so book count measures how much they read, not how good
    it was; and the bonus was the largest modifier in the model (+0.532 for a 15-book
    series, more than any other term's whole cap), reliably lifting long uneven series
    over short excellent ones. Measured: the old term correlated **+0.88 with book
    count** and only +0.24 with avg WA; Consistency correlates **−0.00** with count.
  - **Consistency** = `0.50 × n/(n+2) × ((pct − 0.5) × 2)`, capped ±0.50, where `pct` is
    the share of the reader's rated books that the series' **weakest volume** beats.
    A percentile (not a raw WA gap) makes it scale-free across harsh and generous
    raters; the `n/(n+2)` shrinkage discounts thin evidence smoothly. Needs the library
    distribution — `series_aggregate` supplies it via `views.library_reference`; without
    it the term is 0 and `Weakest Pct` is None rather than invented.
  - This **absorbed a former `Floor` term** (`avg_WA − min_WA`). Both price the weakest
    volume, so keeping both charged a bad book twice (they correlated −0.54), and the
    absolute form is strictly stronger: a *relative* floor scores a uniformly mediocre
    series as perfectly consistent.
  - **Peak** = `0.30 × (max_WA − avg_WA)`, capped +0.35 — it produced a standout. Kept
    as a **deviation from the series' own average**: the level form ("the best book's
    WA") correlates ~0.95 with avg WA and would merely double-count it.
  - **Finale** = `0.15 × (final volume's Ending − mean Ending)`, capped +0.30 / −0.50
    (a botched ending costs more than a great one earns). **Only applied to a series
    the reader has marked complete** — see the `series_meta` note under HARD
    CONSTRAINT 3. An unmarked series gets no Finale term, so an ongoing series is
    never charged for an ending it hasn't written, and `series_aggregate` called
    without `series_meta` suppresses the term everywhere.
  - **Unfinished** = `−0.10` while a series has not ended (owner, 2026-08-17). Small on
    purpose: 0.10 moves two series on the live library, 0.20 moves seven by up to three
    places. It is **separate from the suppressed Finale and both apply** — Finale going
    to 0 means "no evidence about the ending", this means "not having one is itself a
    mark against it".
  - **`complete` is TRI-STATE and the third state is load-bearing.** `True` = finished
    (Finale applies, no charge); `False` = known ongoing (no Finale, charge applies);
    **`None` = the caller had no completeness data at all → no Finale AND no charge**.
    `series_aggregate` passes None exactly when `series_meta` is omitted, so a caller
    that cannot supply the flags never marks the whole library down for missing data it
    never had. Within a supplied `series_meta`, an absent row still means ongoing.
  - **Evidence** = `−0.4` at n=1, **outside** the ±0.75 budget — a one-book "series" has
    no within-series information at all and is held back rather than ranked on a single
    volume. n≥2 takes no penalty; the shrinkage above already handles thin evidence
    without a cliff.
  - The Consistency coefficient settled at **0.50** (0.70 → 0.55 → 0.45 → 0.50, owner,
    2026-08-17), the last move on robustness rather than taste. **There is no
    statistically optimal K** — this is a preference composition with no ground truth to
    fit — but a 0→1 sweep on the live library shows the ranking is *identical across
    0.50–0.80*, a plateau 3× wider than any other, and 0.50 is its lowest point: the
    smallest coefficient that isn't on a cliff. (0.45 sat between order changes at
    0.40→0.45 and 0.45→0.50.) The same sweep ruled out two worries: the term never
    dominates (avg WA has ~8× its spread even at K=1.0; it owns 1.5% of score variance
    here), and rating-noise fragility is flat across 0.25–0.70. The real floor is lower:
    below ~0.45 The Wheel of Time (15 books, weakest volume at the 39th percentile)
    climbs back over Lord of the Rings — the long-series inflation this replaced the
    length bonus to fix. At 0.50 the per-term ±0.50 cap is **inert** (largest real value
    0.338); it is kept as a guard-rail in case K rises, and `_QUALITY_CLAMP` bounds the sum.
  - Regression guard: `test_series_score.py` (52 checks), including the explicit
    no-length-reward guard: a long series with a bad book must score below a short
    excellent one, and `_commitment_term` must stay gone. The cap checks are decoupled
    from the tuned K — one asserts the shipped K never exceeds the cap, the other forces
    K high to prove the clamp still works — so tuning can neither break them spuriously
    nor let them pass vacuously.
  - **Series Breakdown page** (`/series-breakdown`, under the "For Nerds" nav group)
    documents this model term by term, including a section on why there is no length
    bonus, and shows every series' contributions. It reads
    the per-term fields off `GET /api/series` and the COEFFICIENTS off
    `GET /api/engine-parameters` → `series_model` (`engine_parameters._series_model_block`,
    read live from `views`), so a change to any constant here surfaces on the page
    automatically — same anti-drift rule as the Methodology page. Fiction only:
    nonfiction series have no quality model, so there would be no terms to break down.
- **Tier bands:** S+ fixed at Total Average ≥ 9.5 (≥ 9.0 for series); remaining books split
  S/A/B/C/D/F by percentile (~9% / 15% / 25% / 25% / 15% / 10%).
- **Tier spine colors:** S+ #2D6A4F · S #4A7C59 · A #7BA87B · B #D4A853 · C #C07C5A ·
  D #7B8FA1 · F #C4B8AD.

## Pages (frontend/app/)

Top-level: `add-book` · `edit-ratings` · `predict` (+ `predict/genres`) · `read-queue` · `stats` ·
`analytics` · `calibration` · `track-record` · `methodology` · `series-breakdown` ·
`delta-log` · `weights`
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
isn't duplicated, while the public-profile Stats tab keeps it. **Predict is a nav GROUP over two pages**, not one page: `/predict` (**Book Prediction** —
the Discover → score → save flow) and `/predict/genres` (**Genre Prediction** — which genres
the reader's own ratings favour). They answer different questions at different cadences —
the book pass is daily, the genre pass occasional — and stacking them buried the request
box. `Nav.activeItemHref` exists for this pair: Predict is the first group whose item hrefs
NEST, and the old `startsWith` test lit up BOTH entries on `/predict/genres`. Longest match
wins; the mobile list takes the resolved answer via `forceActive`.

Nav lives in
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
