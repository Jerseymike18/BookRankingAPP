# Walk-Forward Backtest — Report

Engine `sha256:72a3d8cdfd15dba9` · git `5701046da784` · 113 folds over 128 books (burn-in 15) · skipped {'BURN_IN': 15}.

Variants: **raw** = grounded research → WA, no correction · **honest** = author+genre correction fit on the *past-only pool* (the walk-forward baseline) · **leaky** = correction fit on the *full library* (today's config; saw future books).

## Overall WA MAE

| variant | WA MAE |
| --- | --- |
| raw (no correction) | 0.822 |
| honest (walk-forward) | 0.631 |
| leaky (today's config) | 0.604 |
| _naive (predict mean WA)_ | 0.858 |

## WA MAE by genre  (raw → honest → leaky; Δ = honest−raw)

| genre | n | raw | honest | leaky | Δ honest−raw |
| --- | --- | --- | --- | --- | --- |
| Russian Literature | 1 | 0.501 | 0.058 | 0.028 | -0.443 |
| Literary Fiction | 1 | 1.473 | 0.346 | 0.368 | -1.128 |
| Gothic Fiction | 2 | 0.476 | 0.446 | 0.475 | -0.030 |
| Science Fiction (Soft) | 17 | 0.629 | 0.480 | 0.358 | -0.149 |
| Epic Fantasy | 51 | 0.750 | 0.498 | 0.553 | -0.252 |
| Science Fantasy | 11 | 0.869 | 0.592 | 0.459 | -0.277 |
| Classical Epic | 2 | 0.899 | 0.754 | 0.716 | -0.145 |
| Science Fiction (Hard) | 11 | 0.774 | 0.768 | 0.860 | -0.005 |
| Classical Drama | 2 | 1.088 | 0.804 | 0.641 | -0.284 |
| Literary Fantasy | 13 | 1.120 | 1.133 | 0.941 | 0.013 |
| Speculative Literary Fiction | 2 | 2.207 | 1.842 | 1.593 | -0.364 |

## WA MAE by year read

| year | n | raw | honest | leaky |
| --- | --- | --- | --- | --- |
| 2025 | 66 | 0.905 | 0.698 | 0.653 |
| 2026 | 47 | 0.706 | 0.538 | 0.536 |

## Rolling WA MAE  (trailing window = 15 folds)

Full per-fold series in `walkforward_rolling_mae.json`. Endpoints:

|  | position | honest rolling | leaky rolling | raw rolling |
| --- | --- | --- | --- | --- |
| first | 16 | 1.005 | 0.150 | 0.503 |
| last | 128 | 0.455 | 0.378 | 0.556 |

## Component MAE — worst first  (WB rows with actual=0 sentinel excluded)

| component | n | raw | honest | leaky | Δ honest−raw |
| --- | --- | --- | --- | --- | --- |
| Ending | 113 | 1.301 | 1.169 | 1.202 | -0.132 |
| Emotional Impact | 113 | 1.145 | 1.055 | 1.004 | -0.090 |
| Integration *(WB)* | 100 | 0.976 | 0.881 | 0.859 | -0.095 |
| Action | 113 | 0.875 | 0.860 | 0.809 | -0.016 |
| Depth | 113 | 0.961 | 0.848 | 0.782 | -0.113 |
| Motivations | 113 | 1.012 | 0.829 | 0.764 | -0.183 |
| Plot | 113 | 0.941 | 0.819 | 0.731 | -0.123 |
| Narration | 113 | 0.994 | 0.818 | 0.726 | -0.176 |
| Entertainment | 113 | 0.912 | 0.815 | 0.761 | -0.098 |
| Thought-Provokingness | 113 | 0.902 | 0.810 | 0.771 | -0.091 |
| Depth2 *(WB)* | 100 | 0.840 | 0.781 | 0.718 | -0.059 |
| Originality *(WB)* | 100 | 0.959 | 0.766 | 0.700 | -0.193 |
| Insights | 113 | 0.858 | 0.727 | 0.709 | -0.131 |
| Prose | 113 | 0.862 | 0.614 | 0.542 | -0.248 |

## Interval coverage  (nominal 90%)

| variant | coverage | n | vs nominal |
| --- | --- | --- | --- |
| raw (no correction) | 16.8% | 113 | -73.2% |
| honest (walk-forward) | 31.0% | 113 | -59.0% |
| leaky (today's config) | 31.0% | 113 | -59.0% |

**Caveat — this is the point-engine's `±1.645·resid_sd` band, and it is overconfident by design.** `resid_sd`≈0.13 is the residual of the near-perfect WA-from-category-averages regression (WA is essentially a deterministic roll-up of the category averages), so the band is only ±0.21 WA — not a real prediction interval for researched components. The **calibrated** interval the app actually serves is the density-bucketed conformal table in `calibration/residuals.json`:

| served conformal interval (bucketed by author analogs) | coverage | n | vs nominal |
| --- | --- | --- | --- |
| honest errors vs `calibration/residuals.json` | 81.4% | 113 | -8.6% |

_(The served table is sized on autonomous-engine LOO residuals; applying it to researched errors is the faithful 'what interval does a reader see at this density' check. Its ~80% target is the honest calibration story; the resid_sd band is not.)_

## Top 10 WA misses — honest variant

| pos | title | genre | pool | actual | pred | signed err | analog | nA/nG |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 82 | The Last Shadow | Science Fiction (Har | 81 | 2.30 | 6.21 | 3.91 | author | 6/10 |
| 32 | Mistborn: Secret History | Literary Fantasy | 31 | 4.40 | 7.62 | 3.23 | author | 8/2 |
| 64 | The Stone of Farewell | Epic Fantasy | 63 | 4.54 | 7.40 | 2.86 | author | 1/28 |
| 68 | Station 11 | Speculative Literary | 67 | 5.15 | 7.83 | 2.68 | genre | 0/1 |
| 55 | The Neverending Story | Literary Fantasy | 54 | 9.33 | 7.40 | -1.93 | genre | 0/7 |
| 35 | Shadows for Silence | Literary Fantasy | 34 | 5.07 | 6.90 | 1.83 | author | 11/5 |
| 115 | The Fifth Season | Science Fiction (Sof | 114 | 6.90 | 8.70 | 1.80 | genre | 0/15 |
| 60 | Heir to the Empire | Science Fantasy | 59 | 5.96 | 7.50 | 1.54 | global | 0/0 |
| 36 | Rhythm of War | Epic Fantasy | 35 | 8.47 | 7.01 | -1.46 | author | 12/10 |
| 91 | Ready Player One | Science Fiction (Sof | 90 | 4.51 | 5.88 | 1.37 | author | 1/14 |

## Raw → corrected: where the correction helps / hurts

- Genres where the walk-forward correction **beats raw**: 10 (best: Literary Fiction, Russian Literature, Speculative Literary Fiction).
- Genres where it **hurts vs raw**: 1 (Literary Fantasy).
- Overall, honest correction changes WA MAE by **-0.191** vs raw (negative = correction helps).

## Reconciliation vs delta_log  (genuine pre-read predictions; informational)

| title | logged | historical pred | harness honest | harness leaky | actual | status |
| --- | --- | --- | --- | --- | --- | --- |
| The Republic of Thieves | 2026-06-30 | 7.51 | 6.93 | 6.93 | 6.82 | evaluated (pos 127) |
| The Wise Man's Fear | 2026-07-04 | 6.73 |   -   |   -   | 5.00 | not in current library |
| The Rise of Endymion | 2026-07-07 | 7.91 | 7.81 | 7.82 | 7.73 | evaluated (pos 128) |

Differences reflect engine/model drift between when each book was really predicted and today's cached-vector re-prediction — expected, not a failure. Rows marked _not in current library_ were predicted + rated historically but are absent from today's `books` table (removed / recategorised), so the harness has no fold for them.

