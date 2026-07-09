# Engine Ablation — Does the Machinery Earn Its Complexity?

Engine `sha256:72a3d8cdfd15dba9` · git `5701046da784` · 113 walk-forward folds (burn-in 15) · zero-API, read-only, deterministic.

## The pre-committed decision rule (fixed before any number was seen)

> The full engine "earns its complexity" if it beats the **author-mean baseline** by **>= 0.05 WA MAE on the >=3-prior-reads subset** AND is not *worse* than author-mean on overall honest MAE. If it beats by 0.02-0.05, verdict is "marginal - complexity is mostly ceremony". If it beats by <0.02 or loses anywhere material, verdict is "the machinery is not paying for itself".

_Verdict below is stated mechanically from the numbers. A "not paying for itself" outcome is a fully valid, valuable result — it is not softened._

## Method — apples-to-apples

Every baseline is scored on the **identical** folds, read order and past-only pool as the engine's `honest` variant. The engine's honest WA errors are **read** from the committed `walkforward_folds.jsonl` (not recomputed, so the engine column is the published 0.63). Baselines are metadata-only — mean WA over the prior-read pool, matching author/genre by exact string equality on the same frame the engine uses — so a baseline's author count **equals** the engine's `n_author` and the `>=N-prior` subsets line up exactly. Baselines are scored with the harness's own per-fold scorer and MAE aggregator; no metric is reimplemented.

`>=N-prior` = the held-out book had **>= N prior-read books by the same author** in its pool (the engine's own `n_author`). Overall is diluted by first-of-author books (n_author=0) where a metadata baseline has nothing author-specific to say and the engine *should* win — the author-prior subsets are the meaningful test.

## WA MAE — baselines vs engine `honest`

| predictor | overall  (n=113) | >=1  (n=80) | >=2  (n=63) | >=3  (n=49) |
| --- | --- | --- | --- | --- |
| global-mean | 0.888 | 0.956 | 0.956 | 0.974 |
| genre-mean | 0.918 | 0.917 | 0.867 | 0.866 |
| **author-mean** | 0.810 | 0.765 | 0.772 | 0.789 |
| author+genre | 0.819 | 0.778 | 0.788 | 0.804 |
| **engine (honest)** | 0.631 | 0.579 | 0.610 | 0.617 |

_Lower is better. `global-mean` is the leakage-safe walk-forward floor (predict the running mean of all prior reads); note it is a stricter, per-fold baseline than `walkforward_report.py`'s `naive_meanWA`, which uses the whole set's mean._

## The gap: engine `honest` MAE − `author-mean` MAE, per subset

| subset | n | engine | author-mean | gap (eng−auth) | who wins |
| --- | --- | --- | --- | --- | --- |
| overall | 113 | 0.631 | 0.810 | -0.179 | engine better |
| >=1 | 80 | 0.579 | 0.765 | -0.186 | engine better |
| >=2 | 63 | 0.610 | 0.772 | -0.162 | engine better |
| >=3 | 49 | 0.617 | 0.789 | -0.172 | engine better |

_Negative gap = the engine's machinery beats just-average-this-author. Positive = the naive author mean is as good or better._

## Per-genre — engine `honest` vs `author-mean`  (widest engine loss first)

| genre | n | engine | author-mean | gap (eng−auth) | sample-size flag |
| --- | --- | --- | --- | --- | --- |
| Speculative Literary Fiction | 2 | 1.842 | 1.084 | 0.758 | ⚠ n<10 — too small to conclude |
| Classical Drama | 2 | 0.804 | 0.261 | 0.544 | ⚠ n<10 — too small to conclude |
| Classical Epic | 2 | 0.754 | 0.693 | 0.061 | ⚠ n<10 — too small to conclude |
| Science Fantasy | 11 | 0.592 | 0.597 | -0.005 |  |
| Russian Literature | 1 | 0.058 | 0.119 | -0.062 | ⚠ n<10 — too small to conclude |
| Epic Fantasy | 51 | 0.498 | 0.608 | -0.110 |  |
| Science Fiction (Soft) | 17 | 0.480 | 0.756 | -0.276 |  |
| Gothic Fiction | 2 | 0.446 | 0.840 | -0.394 | ⚠ n<10 — too small to conclude |
| Science Fiction (Hard) | 11 | 0.768 | 1.172 | -0.404 |  |
| Literary Fantasy | 13 | 1.133 | 1.611 | -0.478 |  |
| Literary Fiction | 1 | 0.346 | 1.422 | -1.076 | ⚠ n<10 — too small to conclude |

_No genre with n>=10 shows the author mean beating the engine by >0.02; the per-genre signal is dominated by small cells (flagged), which the rule forbids from driving the verdict._

**Suspected weak spots (the brief flagged literary / Russian lit):** Speculative Literary Fiction (n=2: author-mean better by 0.758) [n<10, too small]; Russian Literature (n=1: engine better by 0.062) [n<10, too small]; Literary Fantasy (n=13: engine better by 0.478); Literary Fiction (n=1: engine better by 1.076) [n<10, too small]. The only literary/Russian cell(s) at n>=10 (Literary Fantasy) are still won by the engine; every other is n<10 and cannot support a conclusion.

## Verdict (mechanical, from the rule above)

| quantity | value |
| --- | --- |
| engine honest MAE, >=3 subset | 0.617 |
| author-mean MAE, >=3 subset | 0.789 |
| improvement on >=3 (author − engine) | 0.172 |
| engine honest MAE, overall | 0.631 |
| author-mean MAE, overall | 0.810 |
| improvement overall (author − engine) | 0.179 |
| not worse than author-mean overall? | yes |
| loses materially (>=0.02) anywhere? | no |

**VERDICT: THE ENGINE EARNS ITS COMPLEXITY.**

On the >=3-prior-reads subset the engine's honest WA MAE is 0.617 vs author-mean 0.789 — the engine is better by 0.172. Overall the engine is better by 0.179. This clears the pre-committed bar (>= 0.05 on the decision subset and not worse overall): the 14-component machinery extracts signal a naive author mean cannot.

