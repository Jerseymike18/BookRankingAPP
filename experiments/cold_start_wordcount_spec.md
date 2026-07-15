# Spec — Word-count-aware cold-start terminus term

Status: **prototype, default-OFF, on branch `experiment/tabpfn-cold-start-prior`.** Not wired
into the backend/live app; validated on walk-forward only. Requires owner sign-off before any
live wiring.

## Motivation (validated)

On the **cold slice** (`n_author == 0` — a book with no same-author analog), the author+genre
correction is *blind to book length*: with no same-author history it leans on genre+global means,
which don't encode how this reader responds to long books. The honest walk-forward residual
(`actual − honest`) correlates with **word count at r=+0.38** (this reader rates long
literary/epic books above the genre+global expectation).

Adding a small linear word-count term on the cold slice, validated walk-forward:
- **cold-slice WA MAE −0.06 to −0.09**, **overall −0.02 to −0.03** (gated to `n_author==0`).
- **Permutation test p=0.007** (shuffling word counts destroys the gain → real signal, not a snoop).
- **OLS ≈ ridge ≈ TabPFN** → it is the *feature*, not the model. No foundation model needed.
- Caveat: modest and **concentrated** (per-fold sign test 15/27, p=0.56) — it fixes the big
  under-predicted long-book misses rather than improving every book. Small n (26–27 cold folds).

## Design

**Term.** For a book with word count `w`:
```
adj = b0 + b1 · (log10(w) − mu)
wa_cold = wa + adj          # applied ONLY when n_author == 0
```
`(b0, b1, mu)` are fit by OLS on the training pool: `residual ~ (log10(words) − mu)`, where
`residual_b = actual_WA_b − honest_WA_b` and `honest_WA_b` is the correction's own leave-one-out
prediction for pool book `b` (`correct_and_predict` already excludes the target row internally, so
this is LOO for free). Fit on **all** pool books; **applied only** on the cold slice — matching the
validated "fit-on-all, gate-to-cold" recipe.

**Placement.** Two pure functions in `research_predict.py` and a hook in `correct_and_predict`:
- `fit_cold_start_term(books, cache, gw, gcw, corr_models=None) -> coefs|None`
- `apply_cold_start_term(wa, words, coefs) -> wa'` (missing/None word count → unchanged)
- `correct_and_predict(..., corr_models=None, words=None, cold_term=None)`:
  after the WA roll-up, `if cold_term is not None and n_author == 0: wa = apply_cold_start_term(...)`,
  then the interval **center** shifts with `wa` (half-width unchanged) and `rank` is recomputed.

The read-only core is **untouched**: `predict_engine.py`, `db_loader.py`, `views.py`, and
`reresearch_and_measure.correct_book` (the author/genre correction math) are not modified. This is an
additive post-step in the app-facing glue.

**Default-off = byte-identical.** `cold_term` defaults to `None`; every existing caller
(`test_engine`, `walkforward`, `repredict_on_add`, `backend/main`) omits it, so behavior and the
0.636 baseline are unchanged and `test_engine` stays 29/29. The live app would opt in by fitting the
term at engine build and passing it (plus the book's `words`) — **deferred** until validated + approved.

**Interval.** The served conformal band (`intervals.py`) is untouched — it is density-bucketed by
analog count downstream and independent of the prior mean. Only the point estimate moves.

**Guards.** No word count (`None`/0) → no adjustment. Adjusted WA is clamped to `[0, 10]`. `b1` is
only fit when the pool has ≥ N books with word counts (else `cold_term=None`, i.e. off).

## Gating

Applied iff `n_author == 0`. Untouched for `n_author ≥ 1` (the correction already captures length via
analogs there — the validation showed the gain *vanishes* when the gate widens). So every
`n_author ≥ 1` prediction is byte-identical to today.

## Validation plan

1. `experiments/validate_cold_term.py` — full walk-forward: per fold, fit the term on the past pool
   and apply the real `apply_cold_start_term` on cold folds. Report overall / cold / non-cold WA MAE
   vs the 0.636 / 0.746 baselines. Non-cold must be unchanged.
2. Confirm the live-style fit (`fit_cold_start_term` over the full library) yields a slope `b1`
   consistent with the per-fold walk-forward fits.
3. `test_engine.py` 29/29 (default-off).
4. Determinism (fixed inputs → identical output).

## Rollout (only if validation holds; separate, owner-approved step)

Fit at engine build in `backend/main._build_engine_for`, pass `words`+`cold_term` from the predict
endpoints, add the term's `b1`/gate to `engine_parameters` so the methodology page reflects it,
regenerate `calibration/residuals.json`, and re-confirm walk-forward + `test_engine`.
