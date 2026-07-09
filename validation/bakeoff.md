# Walk-Forward Bake-Off — TabPFN Challenger vs Engine Champion

Engine `sha256:72a3d8cdfd15dba9` · git `5701046da784` · 113 folds (burn-in 15) · TabPFN v2 (seed 42, CPU, 8 estimators) · zero-API, read-only, deterministic.

## Champion sanity check

Champion honest WA MAE read from the committed folds: **0.631** (expected ≈ 0.6315). ✅ reproduces the published number — the head-to-head is wired to the same folds.

## Method — apples-to-apples

The champion's honest WA errors are **read** from `walkforward_folds.jsonl` (not recomputed). The challenger is scored on the **identical** folds / read order / strictly-past-only horizon, with the harness's own per-fold scorer and MAE aggregator. TabPFN sees **raw** causal author/genre priors (mean + count), a word-count, and series flag/position — never the engine's shrunk baseline — so this is learned vs. hand-tuned shrinkage. `>=N-prior` = the book had >= N prior-read books by the same author (the engine's own `n_author`), which equals the challenger's `author_prior_count` by construction.

## WA MAE — champion vs challenger

| predictor | overall  (n=113) | >=1  (n=80) | >=2  (n=63) | >=3  (n=49) |
| --- | --- | --- | --- | --- |
| **champion (engine honest)** | 0.631 | 0.579 | 0.610 | 0.617 |
| **challenger (TabPFN)** | 0.698 | 0.634 | 0.613 | 0.577 |
| delta (champ − chal) | -0.067 | -0.054 | -0.003 | 0.040 |

_Lower is better. Positive delta = challenger better. |delta| < 0.02 on overall MAE is a **tie** (small-N fold noise); only a clean margin counts.

## Verdict

| quantity | value |
| --- | --- |
| champion overall WA MAE | 0.631 |
| challenger overall WA MAE | 0.698 |
| delta (champion − challenger) | -0.067 |
| tie threshold | 0.02 |

**VERDICT: CHALLENGER LOSES (champion wins).** The engine beats TabPFN by 0.067 WA MAE (>= 0.02). No ensemble is warranted (a blend of a good and a worse model rarely wins); the bake-off ends here.

## Paired per-book comparison (is a win real or noise?)

| quantity | value |
| --- | --- |
| folds | 113 |
| mean paired |error| diff (champ − chal) | -0.067 |
| median paired diff | -0.055 |
| bootstrap 95% CI of mean diff | [-0.164, 0.031] |
| folds challenger strictly better | 50 |
| folds champion strictly better | 63 |
| ties | 0 |
| champion/challenger residual correlation | 0.732 |

_Mean paired-difference bootstrap (seed 42, 10000 resamples): the CI straddles 0, so the mean difference is not distinguishable from noise. Residual correlation is the Phase-5a ensemble pre-check — near 1.0 means both models make the same mistakes, so a blend is unlikely to help._

## Per-fold absolute-error spread

| predictor | min | p25 | median | p75 | max |
| --- | --- | --- | --- | --- | --- |
| champion | 0.003 | 0.165 | 0.400 | 0.913 | 3.914 |
| challenger | 0.002 | 0.236 | 0.553 | 0.941 | 4.445 |

_Full per-fold arrays live in `bakeoff_predictions.jsonl` (one row per fold, sorted by position)._

## Per-genre — champion vs challenger  (widest challenger loss first)

| genre | n | champion | challenger | gap (chal−champ) | flag |
| --- | --- | --- | --- | --- | --- |
| Russian Literature | 1 | 0.058 | 1.409 | 1.352 | ⚠ n<10 |
| Gothic Fiction | 2 | 0.446 | 1.322 | 0.877 | ⚠ n<10 |
| Classical Drama | 2 | 0.804 | 1.019 | 0.215 | ⚠ n<10 |
| Science Fiction (Hard) | 11 | 0.768 | 0.964 | 0.195 |  |
| Epic Fantasy | 51 | 0.498 | 0.600 | 0.102 |  |
| Science Fiction (Soft) | 17 | 0.480 | 0.557 | 0.077 |  |
| Science Fantasy | 11 | 0.592 | 0.607 | 0.014 |  |
| Literary Fiction | 1 | 0.346 | 0.352 | 0.007 | ⚠ n<10 |
| Classical Epic | 2 | 0.754 | 0.760 | 0.006 | ⚠ n<10 |
| Literary Fantasy | 13 | 1.133 | 0.840 | -0.293 |  |
| Speculative Literary Ficti | 2 | 1.842 | 1.340 | -0.502 | ⚠ n<10 |

_Positive gap = the challenger is worse in that genre. Cells with n<10 are too small to conclude._

## Notes & caveats

- **Mechanically a loss, but a soft one.** The headline is a 0.067 WA MAE loss (> 0.02), so by the pre-committed rule this is a clear loss — not a tie. Yet the paired-difference bootstrap CI straddles 0, so at the per-book level the gap is not statistically clean: a few large-error folds (Russian Lit / Gothic outliers) drive most of it.

- **Where the challenger actually wins: rich author history.** On the >=3-prior-author subset (n=49) TabPFN *beats* the engine by 0.040 (0.577 vs 0.617). Learned shrinkage is competitive exactly where there is enough same-author data to learn from; it loses overall on thin-author books, where the engine's regression backbone + genre-bias carry it and a metadata-only learner has little to go on.

- **Residual correlation 0.732** — moderate, not extreme. The two models do make somewhat different mistakes, but the challenger is the clearly weaker one overall, so blending a weaker model into the champion is not expected to help.

- **Phases 4 (intervals) and 5 (ensemble) are intentionally NOT run.** The brief gates interval calibration on a point-prediction *win* and gates the ensemble ladder on a *win or tie*; a clear aggregate loss triggers neither. Nothing about the served path, the conformal band, or the engine changes — this is an analysis-only bake-off and its verdict is that the champion stays.

