# Prediction-interval coverage — before / after

_Date 2026-07-07 · git `afa3639` · engine `sha256:0c96e1c227afb735` · 113 honest
walk-forward folds (of 128; 15 burn-in excluded)._

## The fix

The served/displayed prediction interval is the **density-bucketed conformal 80%
band** (`calibration/residuals.json`), bucketed by how many same-author analogs the library holds.
The legacy **±1.645·resid_sd "90% CI"** — where `resid_sd` is the residual of the
near-deterministic WA-from-category-averages regression, **not** the residual of
predicting an unread book's WA — was removed from every served surface
(`/api/predict/*` responses, the Predict-page fallback, the Calibration "90% CI"
stat, and the blurb-prompt confidence frame). Point predictions are unchanged.

## Coverage against the honest walk-forward residuals

`validation/walkforward_folds.jsonl` holds, per fold, the honest-variant predicted
WA and the actual WA. Coverage = fraction of folds whose actual WA falls inside the
book's interval, bucketed exactly as the live serving path buckets it.

| interval | claimed | measured coverage | n | verdict |
| --- | --- | --- | --- | --- |
| legacy ±1.645·resid_sd (removed) | 90% | **31.0%** | 113 | badly overconfident |
| served conformal band (kept) | 80% | **81.4%** | 113 | well-calibrated |

The served band claims 80% and delivers 81.4% on out-of-sample honest
errors — essentially on target. (Owner decision: keep the honest 80% level rather
than re-inflating to a nominal 90%.)

## Thin-pool behaviour (degrades sanely)

The band **widens** as the same-author analog pool thins, so a frontier book is
never given a falsely-tight interval. Half-widths from `calibration/residuals.json`:

| density bucket | half-width (WA) | residuals | pooled |
| --- | --- | --- | --- |
| author-rich (`cluster n>=6`) | 0.9209 | 59 | no |
| some author data (`cluster 2<=n<6`) | 0.8471 | 31 | no |
| single author book (`author-only n=1`) | 0.9501 | 12 | yes |
| genre only (`genre-only n=0`) | 1.3439 | 25 | no |

Thin buckets below 20 residuals borrow their nearest-richer
neighbour (pooled = yes); if no residual table is loaded at all, the serving path
omits the interval entirely rather than inventing a width.

## Reproduce

```
python3 -c "import json, walkforward_report as wr; \
  folds=[json.loads(l) for l in open('validation/walkforward_folds.jsonl') if json.loads(l).get('variants')]; \
  print('conformal', wr.served_interval_coverage(folds)); \
  print('resid_sd', wr.interval_coverage(folds)['honest'])"
```
