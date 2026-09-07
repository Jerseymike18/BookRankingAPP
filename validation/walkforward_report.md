# Walk-Forward Backtest — Report

Engine `sha256:c12adfb15c04adcd` · git `325984fdadee` · 127 folds over 143 books (burn-in 15) · skipped {'POOL_LT_BURN_IN': 15, 'SKIPPED_NO_CACHE': 1}.

Variants: **raw** = grounded research → WA, no correction · **honest** = *memory-only* vector, author+genre correction fit on the *past-only pool* (the pre-refine state) · **leaky** = correction fit on the *full library* (today's config; saw future books) · **hybrid** = the **LIVE served** input — memory correction (as honest) on the hybrid vector (memory + web-grounded overrides), i.e. what the app actually serves.

## Overall WA MAE

| variant | WA MAE |
| --- | --- |
| raw (no correction) | 0.780 |
| honest — memory-only (pre-refine) | 0.586 |
| leaky (today's config) | 0.540 |
| hybrid (LIVE served) | 0.552 |
| _naive (predict mean WA)_ | 0.870 |

## Rank correlation — predicted vs actual WA  (held-out folds)

| variant | Spearman ρ | Kendall τ | n |
| --- | --- | --- | --- |
| raw (no correction) | 0.454 | 0.308 | 127 |
| honest — memory-only (pre-refine) | 0.716 | 0.530 | 127 |
| leaky (today's config) | 0.807 | 0.612 | 127 |
| hybrid (LIVE served) | 0.755 | 0.569 | 127 |

_The product ranks books, so order-preservation (ρ, τ) is a first-class adoption metric alongside MAE — a biased-but-monotone model can still rank well. All later phase decisions weigh MAE and rank correlation together._

## WA MAE by genre  (raw → honest → leaky; Δ = honest−raw)

| genre | n | raw | honest | leaky | Δ honest−raw |
| --- | --- | --- | --- | --- | --- |
| Russian Literature | 2 | 0.426 | 0.255 | 0.230 | -0.171 |
| Literary Fiction | 1 | 1.473 | 0.390 | 0.398 | -1.083 |
| Epic Fantasy | 58 | 0.716 | 0.438 | 0.492 | -0.278 |
| Gothic Fiction | 2 | 0.476 | 0.439 | 0.501 | -0.037 |
| Science Fiction (Soft) | 12 | 0.592 | 0.545 | 0.307 | -0.048 |
| Science Fantasy | 13 | 1.000 | 0.557 | 0.425 | -0.443 |
| Science Fiction (Hard) | 18 | 0.589 | 0.629 | 0.600 | 0.039 |
| Classical Epic | 2 | 0.899 | 0.640 | 0.752 | -0.259 |
| Classical Drama | 2 | 1.088 | 0.754 | 0.680 | -0.334 |
| Literary Fantasy | 14 | 1.030 | 1.090 | 0.849 | 0.060 |
| Speculative Literary Fiction | 3 | 1.739 | 1.374 | 1.127 | -0.365 |

## WA MAE by year read

| year | n | raw | honest | leaky |
| --- | --- | --- | --- | --- |
| 2025 | 58 | 0.947 | 0.709 | 0.669 |
| 2026 | 69 | 0.640 | 0.482 | 0.431 |

## Rolling WA MAE  (trailing window = 15 folds)

Full per-fold series in `walkforward_rolling_mae.json`. Endpoints:

|  | position | honest rolling | leaky rolling | raw rolling |
| --- | --- | --- | --- | --- |
| first | 16 | 0.112 | 0.863 | 0.055 |
| last | 143 | 0.411 | 0.396 | 0.669 |

## Component MAE — worst first  (WB rows with actual=0 sentinel excluded)

| component | n | raw | honest | leaky | Δ honest−raw |
| --- | --- | --- | --- | --- | --- |
| Ending | 127 | 1.298 | 1.147 | 1.141 | -0.150 |
| Emotional Impact | 127 | 1.138 | 1.020 | 0.966 | -0.118 |
| Integration *(WB)* | 113 | 0.928 | 0.895 | 0.860 | -0.033 |
| Motivations | 127 | 0.996 | 0.841 | 0.793 | -0.155 |
| Depth2 *(WB)* | 113 | 0.792 | 0.839 | 0.695 | 0.047 |
| Narration | 127 | 0.986 | 0.836 | 0.743 | -0.150 |
| Originality *(WB)* | 113 | 0.943 | 0.821 | 0.677 | -0.123 |
| Action | 127 | 0.874 | 0.811 | 0.770 | -0.063 |
| Plot | 127 | 0.919 | 0.790 | 0.678 | -0.129 |
| Depth | 127 | 0.946 | 0.789 | 0.700 | -0.158 |
| Thought-Provokingness | 127 | 0.882 | 0.779 | 0.705 | -0.103 |
| Entertainment | 127 | 0.884 | 0.768 | 0.702 | -0.116 |
| Insights | 127 | 0.835 | 0.711 | 0.676 | -0.123 |
| Prose | 127 | 0.823 | 0.640 | 0.536 | -0.182 |

## Interval coverage  (nominal 90%)

| variant | coverage | n | vs nominal |
| --- | --- | --- | --- |
| raw (no correction) | 18.1% | 127 | -71.9% |
| honest — memory-only (pre-refine) | 35.4% | 127 | -54.6% |
| leaky (today's config) | 30.7% | 127 | -59.3% |
| hybrid (LIVE served) | 34.6% | 127 | -55.4% |

**Caveat — this is the point-engine's `±1.645·resid_sd` band, and it is overconfident by design.** `resid_sd`≈0.13 is the residual of the near-perfect WA-from-category-averages regression (WA is essentially a deterministic roll-up of the category averages), so the band is only ±0.21 WA — not a real prediction interval for researched components. The **calibrated** interval the app actually serves is the density-bucketed conformal table in `calibration/residuals.json`:

| served conformal interval (bucketed by author analogs) | coverage | n | vs nominal |
| --- | --- | --- | --- |
| honest errors vs `calibration/residuals.json` | 83.5% | 127 | -6.5% |

_(The served table is sized on autonomous-engine LOO residuals; applying it to researched errors is the faithful 'what interval does a reader see at this density' check. Its ~80% target is the honest calibration story; the resid_sd band is not.)_

## Top 10 WA misses — honest variant

| pos | title | genre | pool | actual | pred | signed err | analog | nA/nG |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 24 | Mistborn: Secret History | Literary Fantasy | 23 | 4.40 | 7.78 | 3.38 | author | 8/2 |
| 60 | Station 11 | Speculative Literary | 59 | 5.15 | 8.05 | 2.89 | global | 0/0 |
| 56 | The Stone of Farewell | Epic Fantasy | 55 | 4.54 | 7.40 | 2.86 | author | 1/25 |
| 74 | The Last Shadow | Science Fiction (Har | 73 | 3.78 | 6.41 | 2.63 | author | 6/8 |
| 83 | Ready Player One | Science Fiction (Sof | 82 | 4.51 | 7.08 | 2.57 | genre | 0/13 |
| 47 | The Neverending Story | Literary Fantasy | 46 | 9.29 | 7.36 | -1.94 | genre | 0/7 |
| 27 | Shadows for Silence | Literary Fantasy | 26 | 5.07 | 6.91 | 1.84 | author | 11/5 |
| 107 | The Fifth Season | Science Fiction (Sof | 106 | 6.90 | 8.63 | 1.73 | genre | 0/14 |
| 52 | Heir to the Empire | Science Fantasy | 51 | 5.96 | 7.56 | 1.60 | global | 0/0 |
| 21 | Edgedancer | Literary Fantasy | 20 | 6.09 | 7.63 | 1.55 | author | 6/1 |

## Raw → corrected: where the correction helps / hurts

- Genres where the walk-forward correction **beats raw**: 9 (best: Literary Fiction, Science Fantasy, Speculative Literary Fiction).
- Genres where it **hurts vs raw**: 2 (Literary Fantasy, Science Fiction (Hard)).
- Overall, honest correction changes WA MAE by **-0.195** vs raw (negative = correction helps).

## Reconciliation vs delta_log  (genuine pre-read predictions; informational)

| title | logged | historical pred | harness honest | harness leaky | actual | status |
| --- | --- | --- | --- | --- | --- | --- |
| The Republic of Thieves | 2026-06-30 | 7.51 | 6.95 | 6.98 | 6.82 | evaluated (pos 127) |
| The Wise Man's Fear | 2026-07-04 | 6.73 | 7.17 | 7.23 | 5.00 | evaluated (pos 132) |
| The Rise of Endymion | 2026-07-07 | 7.91 | 7.76 | 7.76 | 7.73 | evaluated (pos 128) |
| The Obelisk Gate | 2026-07-11 | 6.64 | 6.32 | 6.23 | 5.84 | evaluated (pos 129) |
| Lord of Emperors | 2026-07-19 | 8.17 | 8.08 | 8.10 | 8.37 | evaluated (pos 130) |
| The Stone Sky | 2026-07-23 | 7.24 | 6.72 | 6.71 | 6.89 | evaluated (pos 131) |
| The Wise Man's Fear | 2026-07-29 | 6.69 | 7.17 | 7.23 | 8.09 | evaluated (pos 132) |
| Shadow of the Hegemon | 2026-07-31 | 5.84 | 6.36 | 6.59 | 6.40 | evaluated (pos 133) |
| Shadow Puppets | 2026-08-01 | 5.72 | 5.53 | 5.60 | 6.75 | evaluated (pos 134) |
| Shadow of the Giant | 2026-08-01 | 7.03 | 6.84 | 6.86 | 7.17 | evaluated (pos 135) |
| Ender in Exile | 2026-08-01 | 6.30 |   -   |   -   | 6.88 | not in current library |
| A Game of Thrones | 2026-08-04 | 8.41 | 8.55 | 9.07 | 8.55 | evaluated (pos 137) |
| A Clash of Kings | 2026-08-06 | 8.12 | 8.03 | 8.49 | 8.60 | evaluated (pos 138) |
| A Storm of Swords | 2026-08-13 | 9.03 | 9.11 | 9.35 | 9.14 | evaluated (pos 139) |
| The Three-Body Problem | 2026-08-14 | 7.53 | 7.67 | 8.06 | 7.82 | evaluated (pos 140) |
| A Feast for Crows | 2026-08-20 | 7.23 | 7.17 | 7.43 | 7.62 | evaluated (pos 141) |
| A Dance with Dragons | 2026-08-28 | 7.50 | 7.50 | 7.53 | 8.44 | evaluated (pos 142) |
| The Dark Forest | 2026-09-03 | 8.27 | 8.37 | 8.39 | 8.85 | evaluated (pos 143) |

Differences reflect engine/model drift between when each book was really predicted and today's cached-vector re-prediction — expected, not a failure. Rows marked _not in current library_ were predicted + rated historically but are absent from today's `books` table (removed / recategorised), so the harness has no fold for them.

