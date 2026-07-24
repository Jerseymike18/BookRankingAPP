# Walk-Forward Backtest — Report

Engine `sha256:681f0e6a4fd83f53` · git `7b0d70a2c450` · 116 folds over 131 books (burn-in 15) · skipped {'POOL_LT_BURN_IN': 15}.

Variants: **raw** = grounded research → WA, no correction · **honest** = *memory-only* vector, author+genre correction fit on the *past-only pool* (the pre-refine state) · **leaky** = correction fit on the *full library* (today's config; saw future books) · **hybrid** = the **LIVE served** input — memory correction (as honest) on the hybrid vector (memory + web-grounded overrides), i.e. what the app actually serves.

## Overall WA MAE

| variant | WA MAE |
| --- | --- |
| raw (no correction) | 0.826 |
| honest — memory-only (pre-refine) | 0.628 |
| leaky (today's config) | 0.585 |
| hybrid (LIVE served) | 0.589 |
| _naive (predict mean WA)_ | 0.913 |

## Rank correlation — predicted vs actual WA  (held-out folds)

| variant | Spearman ρ | Kendall τ | n |
| --- | --- | --- | --- |
| raw (no correction) | 0.437 | 0.296 | 116 |
| honest — memory-only (pre-refine) | 0.690 | 0.505 | 116 |
| leaky (today's config) | 0.773 | 0.580 | 116 |
| hybrid (LIVE served) | 0.738 | 0.551 | 116 |

_The product ranks books, so order-preservation (ρ, τ) is a first-class adoption metric alongside MAE — a biased-but-monotone model can still rank well. All later phase decisions weigh MAE and rank correlation together._

## WA MAE by genre  (raw → honest → leaky; Δ = honest−raw)

| genre | n | raw | honest | leaky | Δ honest−raw |
| --- | --- | --- | --- | --- | --- |
| Russian Literature | 2 | 0.426 | 0.291 | 0.221 | -0.135 |
| Literary Fiction | 1 | 1.473 | 0.367 | 0.336 | -1.107 |
| Gothic Fiction | 2 | 0.476 | 0.450 | 0.490 | -0.026 |
| Epic Fantasy | 52 | 0.743 | 0.475 | 0.531 | -0.268 |
| Science Fiction (Soft) | 12 | 0.592 | 0.555 | 0.336 | -0.038 |
| Science Fantasy | 13 | 1.000 | 0.605 | 0.483 | -0.395 |
| Classical Epic | 2 | 0.899 | 0.626 | 0.688 | -0.273 |
| Classical Drama | 2 | 1.088 | 0.745 | 0.621 | -0.343 |
| Science Fiction (Hard) | 13 | 0.758 | 0.757 | 0.782 | -0.001 |
| Literary Fantasy | 14 | 1.056 | 1.090 | 0.876 | 0.034 |
| Speculative Literary Fiction | 3 | 1.739 | 1.324 | 1.058 | -0.414 |

## WA MAE by year read

| year | n | raw | honest | leaky |
| --- | --- | --- | --- | --- |
| 2025 | 58 | 0.947 | 0.721 | 0.704 |
| 2026 | 58 | 0.706 | 0.535 | 0.467 |

## Rolling WA MAE  (trailing window = 15 folds)

Full per-fold series in `walkforward_rolling_mae.json`. Endpoints:

|  | position | honest rolling | leaky rolling | raw rolling |
| --- | --- | --- | --- | --- |
| first | 16 | 0.167 | 0.909 | 0.055 |
| last | 131 | 0.359 | 0.269 | 0.637 |

## Component MAE — worst first  (WB rows with actual=0 sentinel excluded)

| component | n | raw | honest | leaky | Δ honest−raw |
| --- | --- | --- | --- | --- | --- |
| Ending | 116 | 1.323 | 1.200 | 1.198 | -0.124 |
| Emotional Impact | 116 | 1.142 | 1.056 | 0.995 | -0.086 |
| Narration | 116 | 1.042 | 0.869 | 0.782 | -0.173 |
| Plot | 116 | 0.953 | 0.844 | 0.743 | -0.109 |
| Motivations | 116 | 0.996 | 0.834 | 0.771 | -0.161 |
| Action | 116 | 0.900 | 0.832 | 0.807 | -0.068 |
| Integration *(WB)* | 101 | 1.016 | 0.831 | 0.832 | -0.185 |
| Depth2 *(WB)* | 101 | 0.872 | 0.830 | 0.728 | -0.042 |
| Entertainment | 116 | 0.958 | 0.830 | 0.764 | -0.128 |
| Thought-Provokingness | 116 | 0.901 | 0.812 | 0.747 | -0.089 |
| Depth | 116 | 0.931 | 0.808 | 0.739 | -0.123 |
| Originality *(WB)* | 101 | 0.959 | 0.785 | 0.670 | -0.174 |
| Insights | 116 | 0.854 | 0.709 | 0.673 | -0.146 |
| Prose | 116 | 0.858 | 0.657 | 0.561 | -0.200 |

## Interval coverage  (nominal 90%)

| variant | coverage | n | vs nominal |
| --- | --- | --- | --- |
| raw (no correction) | 17.2% | 116 | -72.8% |
| honest — memory-only (pre-refine) | 36.2% | 116 | -53.8% |
| leaky (today's config) | 33.6% | 116 | -56.4% |
| hybrid (LIVE served) | 33.6% | 116 | -56.4% |

**Caveat — this is the point-engine's `±1.645·resid_sd` band, and it is overconfident by design.** `resid_sd`≈0.13 is the residual of the near-perfect WA-from-category-averages regression (WA is essentially a deterministic roll-up of the category averages), so the band is only ±0.21 WA — not a real prediction interval for researched components. The **calibrated** interval the app actually serves is the density-bucketed conformal table in `calibration/residuals.json`:

| served conformal interval (bucketed by author analogs) | coverage | n | vs nominal |
| --- | --- | --- | --- |
| honest errors vs `calibration/residuals.json` | 81.9% | 116 | -8.1% |

_(The served table is sized on autonomous-engine LOO residuals; applying it to researched errors is the faithful 'what interval does a reader see at this density' check. Its ~80% target is the honest calibration story; the resid_sd band is not.)_

## Top 10 WA misses — honest variant

| pos | title | genre | pool | actual | pred | signed err | analog | nA/nG |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 74 | The Last Shadow | Science Fiction (Har | 73 | 2.30 | 6.28 | 3.98 | author | 6/8 |
| 24 | Mistborn: Secret History | Literary Fantasy | 23 | 4.40 | 7.73 | 3.33 | author | 8/2 |
| 60 | Station 11 | Speculative Literary | 59 | 5.15 | 8.00 | 2.85 | global | 0/0 |
| 56 | The Stone of Farewell | Epic Fantasy | 55 | 4.54 | 7.38 | 2.84 | author | 1/25 |
| 83 | Ready Player One | Science Fiction (Sof | 82 | 4.51 | 7.04 | 2.53 | genre | 0/13 |
| 47 | The Neverending Story | Literary Fantasy | 46 | 9.33 | 7.34 | -1.99 | genre | 0/7 |
| 27 | Shadows for Silence | Literary Fantasy | 26 | 5.07 | 6.89 | 1.82 | author | 11/5 |
| 107 | The Fifth Season | Science Fiction (Sof | 106 | 6.90 | 8.64 | 1.74 | genre | 0/14 |
| 52 | Heir to the Empire | Science Fantasy | 51 | 5.96 | 7.55 | 1.59 | global | 0/0 |
| 21 | Edgedancer | Literary Fantasy | 20 | 6.09 | 7.56 | 1.47 | author | 6/1 |

## Raw → corrected: where the correction helps / hurts

- Genres where the walk-forward correction **beats raw**: 10 (best: Literary Fiction, Speculative Literary Fiction, Science Fantasy).
- Genres where it **hurts vs raw**: 1 (Literary Fantasy).
- Overall, honest correction changes WA MAE by **-0.198** vs raw (negative = correction helps).

## Reconciliation vs delta_log  (genuine pre-read predictions; informational)

| title | logged | historical pred | harness honest | harness leaky | actual | status |
| --- | --- | --- | --- | --- | --- | --- |
| The Republic of Thieves | 2026-06-30 | 7.51 | 6.93 | 6.94 | 6.82 | evaluated (pos 127) |
| The Wise Man's Fear | 2026-07-04 | 6.73 |   -   |   -   | 5.00 | not in current library |
| The Rise of Endymion | 2026-07-07 | 7.91 | 7.81 | 7.81 | 7.73 | evaluated (pos 128) |
| The Obelisk Gate | 2026-07-11 | 6.64 | 7.05 | 7.01 | 5.84 | evaluated (pos 129) |

Differences reflect engine/model drift between when each book was really predicted and today's cached-vector re-prediction — expected, not a failure. Rows marked _not in current library_ were predicted + rated historically but are absent from today's `books` table (removed / recategorised), so the harness has no fold for them.

