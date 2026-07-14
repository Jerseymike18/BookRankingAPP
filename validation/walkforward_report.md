# Walk-Forward Backtest — Report

Engine `sha256:6c332077317bde82` · git `b428a2c39c50` · 114 folds over 129 books (burn-in 15) · skipped {'BURN_IN': 15}.

Variants: **raw** = grounded research → WA, no correction · **honest** = author+genre correction fit on the *past-only pool* (the walk-forward baseline) · **leaky** = correction fit on the *full library* (today's config; saw future books).

## Overall WA MAE

| variant | WA MAE |
| --- | --- |
| raw (no correction) | 0.831 |
| honest (walk-forward) | 0.636 |
| leaky (today's config) | 0.608 |
| _naive (predict mean WA)_ | 0.868 |

## WA MAE by genre  (raw → honest → leaky; Δ = honest−raw)

| genre | n | raw | honest | leaky | Δ honest−raw |
| --- | --- | --- | --- | --- | --- |
| Russian Literature | 1 | 0.501 | 0.058 | 0.017 | -0.443 |
| Literary Fiction | 1 | 1.473 | 0.346 | 0.348 | -1.128 |
| Gothic Fiction | 2 | 0.476 | 0.446 | 0.481 | -0.030 |
| Science Fiction (Soft) | 17 | 0.629 | 0.479 | 0.357 | -0.149 |
| Epic Fantasy | 51 | 0.750 | 0.498 | 0.551 | -0.252 |
| Science Fantasy | 12 | 0.954 | 0.643 | 0.525 | -0.311 |
| Classical Epic | 2 | 0.899 | 0.754 | 0.700 | -0.145 |
| Science Fiction (Hard) | 11 | 0.774 | 0.768 | 0.857 | -0.005 |
| Classical Drama | 2 | 1.088 | 0.804 | 0.627 | -0.284 |
| Literary Fantasy | 13 | 1.120 | 1.133 | 0.944 | 0.013 |
| Speculative Literary Fiction | 2 | 2.207 | 1.842 | 1.582 | -0.364 |

## WA MAE by year read

| year | n | raw | honest | leaky |
| --- | --- | --- | --- | --- |
| 2025 | 66 | 0.905 | 0.698 | 0.652 |
| 2026 | 48 | 0.730 | 0.551 | 0.548 |

## Rolling WA MAE  (trailing window = 15 folds)

Full per-fold series in `walkforward_rolling_mae.json`. Endpoints:

|  | position | honest rolling | leaky rolling | raw rolling |
| --- | --- | --- | --- | --- |
| first | 16 | 1.005 | 0.146 | 0.503 |
| last | 129 | 0.490 | 0.450 | 0.660 |

## Component MAE — worst first  (WB rows with actual=0 sentinel excluded)

| component | n | raw | honest | leaky | Δ honest−raw |
| --- | --- | --- | --- | --- | --- |
| Ending | 114 | 1.311 | 1.186 | 1.222 | -0.125 |
| Emotional Impact | 114 | 1.146 | 1.049 | 0.998 | -0.097 |
| Integration *(WB)* | 101 | 0.972 | 0.875 | 0.853 | -0.097 |
| Action | 114 | 0.878 | 0.860 | 0.809 | -0.018 |
| Narration | 114 | 1.025 | 0.841 | 0.749 | -0.184 |
| Depth | 114 | 0.958 | 0.840 | 0.774 | -0.118 |
| Plot | 114 | 0.956 | 0.832 | 0.747 | -0.123 |
| Entertainment | 114 | 0.939 | 0.830 | 0.776 | -0.109 |
| Motivations | 114 | 1.011 | 0.822 | 0.758 | -0.189 |
| Thought-Provokingness | 114 | 0.909 | 0.813 | 0.774 | -0.095 |
| Depth2 *(WB)* | 101 | 0.840 | 0.775 | 0.712 | -0.065 |
| Originality *(WB)* | 101 | 0.951 | 0.755 | 0.693 | -0.197 |
| Insights | 114 | 0.862 | 0.728 | 0.710 | -0.134 |
| Prose | 114 | 0.869 | 0.613 | 0.540 | -0.256 |

## Interval coverage  (nominal 90%)

| variant | coverage | n | vs nominal |
| --- | --- | --- | --- |
| raw (no correction) | 16.7% | 114 | -73.3% |
| honest (walk-forward) | 30.7% | 114 | -59.3% |
| leaky (today's config) | 29.8% | 114 | -60.2% |

**Caveat — this is the point-engine's `±1.645·resid_sd` band, and it is overconfident by design.** `resid_sd`≈0.13 is the residual of the near-perfect WA-from-category-averages regression (WA is essentially a deterministic roll-up of the category averages), so the band is only ±0.21 WA — not a real prediction interval for researched components. The **calibrated** interval the app actually serves is the density-bucketed conformal table in `calibration/residuals.json`:

| served conformal interval (bucketed by author analogs) | coverage | n | vs nominal |
| --- | --- | --- | --- |
| honest errors vs `calibration/residuals.json` | 81.6% | 114 | -8.4% |

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
- Overall, honest correction changes WA MAE by **-0.195** vs raw (negative = correction helps).

## Reconciliation vs delta_log  (genuine pre-read predictions; informational)

| title | logged | historical pred | harness honest | harness leaky | actual | status |
| --- | --- | --- | --- | --- | --- | --- |
| The Republic of Thieves | 2026-06-30 | 7.51 | 6.94 | 6.93 | 6.82 | evaluated (pos 128) |
| The Wise Man's Fear | 2026-07-04 | 6.73 |   -   |   -   | 5.00 | not in current library |
| The Rise of Endymion | 2026-07-07 | 7.91 | 7.80 | 7.80 | 7.73 | evaluated (pos 129) |
| The Obelisk Gate | 2026-07-11 | 6.64 | 7.05 | 7.04 | 5.84 | evaluated (pos 127) |

Differences reflect engine/model drift between when each book was really predicted and today's cached-vector re-prediction — expected, not a failure. Rows marked _not in current library_ were predicted + rated historically but are absent from today's `books` table (removed / recategorised), so the harness has no fold for them.

