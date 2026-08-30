# Walk-Forward Backtest — Report

Engine `sha256:9d2b7c25bb42314b` · git `2a43f715cde3` · 125 folds over 141 books (burn-in 15) · skipped {'POOL_LT_BURN_IN': 15, 'SKIPPED_NO_CACHE': 1}.

Variants: **raw** = grounded research → WA, no correction · **honest** = *memory-only* vector, author+genre correction fit on the *past-only pool* (the pre-refine state) · **leaky** = correction fit on the *full library* (today's config; saw future books) · **hybrid** = the **LIVE served** input — memory correction (as honest) on the hybrid vector (memory + web-grounded overrides), i.e. what the app actually serves.

## Overall WA MAE

| variant | WA MAE |
| --- | --- |
| raw (no correction) | 0.783 |
| honest — memory-only (pre-refine) | 0.587 |
| leaky (today's config) | 0.540 |
| hybrid (LIVE served) | 0.550 |
| _naive (predict mean WA)_ | 0.873 |

## Rank correlation — predicted vs actual WA  (held-out folds)

| variant | Spearman ρ | Kendall τ | n |
| --- | --- | --- | --- |
| raw (no correction) | 0.453 | 0.307 | 125 |
| honest — memory-only (pre-refine) | 0.716 | 0.530 | 125 |
| leaky (today's config) | 0.806 | 0.611 | 125 |
| hybrid (LIVE served) | 0.757 | 0.571 | 125 |

_The product ranks books, so order-preservation (ρ, τ) is a first-class adoption metric alongside MAE — a biased-but-monotone model can still rank well. All later phase decisions weigh MAE and rank correlation together._

## WA MAE by genre  (raw → honest → leaky; Δ = honest−raw)

| genre | n | raw | honest | leaky | Δ honest−raw |
| --- | --- | --- | --- | --- | --- |
| Russian Literature | 2 | 0.426 | 0.258 | 0.240 | -0.168 |
| Literary Fiction | 1 | 1.473 | 0.382 | 0.381 | -1.091 |
| Epic Fantasy | 57 | 0.714 | 0.437 | 0.497 | -0.277 |
| Gothic Fiction | 2 | 0.476 | 0.442 | 0.506 | -0.035 |
| Science Fiction (Soft) | 12 | 0.592 | 0.541 | 0.305 | -0.051 |
| Science Fantasy | 13 | 1.000 | 0.555 | 0.426 | -0.444 |
| Classical Epic | 2 | 0.899 | 0.628 | 0.734 | -0.271 |
| Science Fiction (Hard) | 17 | 0.596 | 0.638 | 0.596 | 0.041 |
| Classical Drama | 2 | 1.088 | 0.755 | 0.672 | -0.333 |
| Literary Fantasy | 14 | 1.030 | 1.092 | 0.841 | 0.062 |
| Speculative Literary Fiction | 3 | 1.739 | 1.372 | 1.121 | -0.367 |

## WA MAE by year read

| year | n | raw | honest | leaky |
| --- | --- | --- | --- | --- |
| 2025 | 58 | 0.954 | 0.717 | 0.675 |
| 2026 | 67 | 0.634 | 0.475 | 0.423 |

## Rolling WA MAE  (trailing window = 15 folds)

Full per-fold series in `walkforward_rolling_mae.json`. Endpoints:

|  | position | honest rolling | leaky rolling | raw rolling |
| --- | --- | --- | --- | --- |
| first | 16 | 0.144 | 0.893 | 0.055 |
| last | 141 | 0.332 | 0.326 | 0.607 |

## Component MAE — worst first  (WB rows with actual=0 sentinel excluded)

| component | n | raw | honest | leaky | Δ honest−raw |
| --- | --- | --- | --- | --- | --- |
| Ending | 125 | 1.298 | 1.143 | 1.127 | -0.155 |
| Emotional Impact | 125 | 1.135 | 1.023 | 0.978 | -0.112 |
| Integration *(WB)* | 111 | 0.926 | 0.837 | 0.807 | -0.089 |
| Motivations | 125 | 0.989 | 0.833 | 0.777 | -0.156 |
| Narration | 125 | 0.984 | 0.821 | 0.719 | -0.163 |
| Action | 125 | 0.874 | 0.802 | 0.770 | -0.072 |
| Depth | 125 | 0.953 | 0.800 | 0.716 | -0.152 |
| Depth2 *(WB)* | 111 | 0.795 | 0.795 | 0.653 | 0.000 |
| Originality *(WB)* | 111 | 0.950 | 0.790 | 0.648 | -0.159 |
| Thought-Provokingness | 125 | 0.893 | 0.783 | 0.713 | -0.109 |
| Plot | 125 | 0.919 | 0.782 | 0.679 | -0.138 |
| Entertainment | 125 | 0.882 | 0.771 | 0.708 | -0.111 |
| Insights | 125 | 0.842 | 0.714 | 0.677 | -0.128 |
| Prose | 125 | 0.822 | 0.637 | 0.523 | -0.185 |

## Interval coverage  (nominal 90%)

| variant | coverage | n | vs nominal |
| --- | --- | --- | --- |
| raw (no correction) | 18.4% | 125 | -71.6% |
| honest — memory-only (pre-refine) | 36.0% | 125 | -54.0% |
| leaky (today's config) | 30.4% | 125 | -59.6% |
| hybrid (LIVE served) | 34.4% | 125 | -55.6% |

**Caveat — this is the point-engine's `±1.645·resid_sd` band, and it is overconfident by design.** `resid_sd`≈0.13 is the residual of the near-perfect WA-from-category-averages regression (WA is essentially a deterministic roll-up of the category averages), so the band is only ±0.21 WA — not a real prediction interval for researched components. The **calibrated** interval the app actually serves is the density-bucketed conformal table in `calibration/residuals.json`:

| served conformal interval (bucketed by author analogs) | coverage | n | vs nominal |
| --- | --- | --- | --- |
| honest errors vs `calibration/residuals.json` | 84.0% | 125 | -6.0% |

_(The served table is sized on autonomous-engine LOO residuals; applying it to researched errors is the faithful 'what interval does a reader see at this density' check. Its ~80% target is the honest calibration story; the resid_sd band is not.)_

## Top 10 WA misses — honest variant

| pos | title | genre | pool | actual | pred | signed err | analog | nA/nG |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 24 | Mistborn: Secret History | Literary Fantasy | 23 | 4.40 | 7.78 | 3.38 | author | 8/2 |
| 60 | Station 11 | Speculative Literary | 59 | 5.15 | 8.04 | 2.89 | global | 0/0 |
| 56 | The Stone of Farewell | Epic Fantasy | 55 | 4.54 | 7.39 | 2.85 | author | 1/25 |
| 74 | The Last Shadow | Science Fiction (Har | 73 | 3.78 | 6.41 | 2.63 | author | 6/8 |
| 83 | Ready Player One | Science Fiction (Sof | 82 | 4.51 | 7.06 | 2.55 | genre | 0/13 |
| 47 | The Neverending Story | Literary Fantasy | 46 | 9.29 | 7.35 | -1.94 | genre | 0/7 |
| 27 | Shadows for Silence | Literary Fantasy | 26 | 5.07 | 6.92 | 1.85 | author | 11/5 |
| 107 | The Fifth Season | Science Fiction (Sof | 106 | 6.90 | 8.64 | 1.74 | genre | 0/14 |
| 52 | Heir to the Empire | Science Fantasy | 51 | 5.96 | 7.54 | 1.58 | global | 0/0 |
| 21 | Edgedancer | Literary Fantasy | 20 | 6.09 | 7.64 | 1.55 | author | 6/1 |

## Raw → corrected: where the correction helps / hurts

- Genres where the walk-forward correction **beats raw**: 9 (best: Literary Fiction, Science Fantasy, Speculative Literary Fiction).
- Genres where it **hurts vs raw**: 2 (Literary Fantasy, Science Fiction (Hard)).
- Overall, honest correction changes WA MAE by **-0.195** vs raw (negative = correction helps).

## Reconciliation vs delta_log  (genuine pre-read predictions; informational)

| title | logged | historical pred | harness honest | harness leaky | actual | status |
| --- | --- | --- | --- | --- | --- | --- |
| The Republic of Thieves | 2026-06-30 | 7.51 | 6.95 | 6.98 | 6.82 | evaluated (pos 127) |
| The Wise Man's Fear | 2026-07-04 | 6.73 | 7.17 | 7.22 | 5.00 | evaluated (pos 132) |
| The Rise of Endymion | 2026-07-07 | 7.91 | 7.75 | 7.76 | 7.73 | evaluated (pos 128) |
| The Obelisk Gate | 2026-07-11 | 6.64 | 6.32 | 6.22 | 5.84 | evaluated (pos 129) |
| Lord of Emperors | 2026-07-19 | 8.17 | 8.07 | 8.10 | 8.37 | evaluated (pos 130) |
| The Stone Sky | 2026-07-23 | 7.24 | 6.72 | 6.71 | 6.89 | evaluated (pos 131) |
| The Wise Man's Fear | 2026-07-29 | 6.69 | 7.17 | 7.22 | 8.09 | evaluated (pos 132) |
| Shadow of the Hegemon | 2026-07-31 | 5.84 | 6.36 | 6.59 | 6.40 | evaluated (pos 133) |
| Shadow Puppets | 2026-08-01 | 5.72 | 5.53 | 5.61 | 6.75 | evaluated (pos 134) |
| Shadow of the Giant | 2026-08-01 | 7.03 | 6.84 | 6.85 | 7.17 | evaluated (pos 135) |
| Ender in Exile | 2026-08-01 | 6.30 |   -   |   -   | 6.88 | not in current library |
| A Game of Thrones | 2026-08-04 | 8.41 | 8.54 | 8.88 | 8.55 | evaluated (pos 137) |
| A Clash of Kings | 2026-08-06 | 8.12 | 8.03 | 8.26 | 8.60 | evaluated (pos 138) |
| A Storm of Swords | 2026-08-13 | 9.03 | 9.10 | 9.20 | 9.14 | evaluated (pos 139) |
| The Three-Body Problem | 2026-08-14 | 7.53 | 7.66 | 7.66 | 7.82 | evaluated (pos 140) |
| A Feast for Crows | 2026-08-20 | 7.23 | 7.16 | 7.20 | 7.62 | evaluated (pos 141) |

Differences reflect engine/model drift between when each book was really predicted and today's cached-vector re-prediction — expected, not a failure. Rows marked _not in current library_ were predicted + rated historically but are absent from today's `books` table (removed / recategorised), so the harness has no fold for them.

