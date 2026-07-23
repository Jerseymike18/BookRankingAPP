# Walk-Forward Backtest — Report

Engine `sha256:681f0e6a4fd83f53` · git `9d8640c025d1` · 126 folds over 129 books (burn-in 3) · skipped {'BURN_IN': 3}.

Variants: **raw** = grounded research → WA, no correction · **honest** = author+genre correction fit on the *past-only pool* (the walk-forward baseline) · **leaky** = correction fit on the *full library* (today's config; saw future books).

## Overall WA MAE

| variant | WA MAE |
| --- | --- |
| raw (no correction) | 0.832 |
| honest (walk-forward) | 0.664 |
| leaky (today's config) | 0.611 |
| _naive (predict mean WA)_ | 0.888 |

## WA MAE by genre  (raw → honest → leaky; Δ = honest−raw)

| genre | n | raw | honest | leaky | Δ honest−raw |
| --- | --- | --- | --- | --- | --- |
| Cyberpunk | 1 | 0.180 | 0.193 | 0.337 | 0.012 |
| Gothic Fiction | 2 | 0.476 | 0.446 | 0.481 | -0.030 |
| Science Fiction (Soft) | 17 | 0.629 | 0.479 | 0.356 | -0.149 |
| Epic Fantasy | 53 | 0.728 | 0.494 | 0.533 | -0.235 |
| Classical Epic | 3 | 0.715 | 0.625 | 0.724 | -0.090 |
| Science Fantasy | 12 | 0.954 | 0.643 | 0.525 | -0.311 |
| Science Fiction (Hard) | 14 | 0.724 | 0.718 | 0.776 | -0.005 |
| Classical Drama | 2 | 1.088 | 0.804 | 0.627 | -0.284 |
| Russian Literature | 3 | 0.339 | 0.896 | 0.231 | 0.557 |
| Literary Fantasy | 13 | 1.120 | 1.133 | 0.944 | 0.013 |
| Literary Fiction | 4 | 2.132 | 1.490 | 1.181 | -0.642 |
| Speculative Literary Fiction | 2 | 2.207 | 1.842 | 1.582 | -0.364 |

## WA MAE by year read

| year | n | raw | honest | leaky |
| --- | --- | --- | --- | --- |
| 2025 | 73 | 0.928 | 0.740 | 0.677 |
| 2026 | 53 | 0.700 | 0.560 | 0.522 |

## Rolling WA MAE  (trailing window = 15 folds)

Full per-fold series in `walkforward_rolling_mae.json`. Endpoints:

|  | position | honest rolling | leaky rolling | raw rolling |
| --- | --- | --- | --- | --- |
| first | 4 | 0.377 | 0.076 | 0.321 |
| last | 129 | 0.491 | 0.451 | 0.660 |

## Component MAE — worst first  (WB rows with actual=0 sentinel excluded)

| component | n | raw | honest | leaky | Δ honest−raw |
| --- | --- | --- | --- | --- | --- |
| Ending | 126 | 1.340 | 1.230 | 1.205 | -0.109 |
| Emotional Impact | 126 | 1.181 | 1.084 | 1.022 | -0.096 |
| Plot | 126 | 0.978 | 0.922 | 0.783 | -0.056 |
| Motivations | 126 | 1.083 | 0.888 | 0.821 | -0.196 |
| Narration | 126 | 1.042 | 0.870 | 0.790 | -0.172 |
| Integration *(WB)* | 106 | 0.967 | 0.857 | 0.815 | -0.110 |
| Entertainment | 126 | 0.950 | 0.857 | 0.789 | -0.093 |
| Action | 126 | 0.861 | 0.848 | 0.776 | -0.014 |
| Originality *(WB)* | 106 | 0.939 | 0.845 | 0.685 | -0.094 |
| Depth | 126 | 0.956 | 0.843 | 0.772 | -0.114 |
| Depth2 *(WB)* | 106 | 0.829 | 0.805 | 0.700 | -0.024 |
| Thought-Provokingness | 126 | 0.879 | 0.792 | 0.757 | -0.087 |
| Insights | 126 | 0.847 | 0.713 | 0.684 | -0.134 |
| Prose | 126 | 0.878 | 0.653 | 0.572 | -0.225 |

## Interval coverage  (nominal 90%)

| variant | coverage | n | vs nominal |
| --- | --- | --- | --- |
| raw (no correction) | 19.0% | 126 | -71.0% |
| honest (walk-forward) | 32.5% | 126 | -57.5% |
| leaky (today's config) | 31.0% | 126 | -59.0% |

**Caveat — this is the point-engine's `±1.645·resid_sd` band, and it is overconfident by design.** `resid_sd`≈0.13 is the residual of the near-perfect WA-from-category-averages regression (WA is essentially a deterministic roll-up of the category averages), so the band is only ±0.21 WA — not a real prediction interval for researched components. The **calibrated** interval the app actually serves is the density-bucketed conformal table in `calibration/residuals.json`:

| served conformal interval (bucketed by author analogs) | coverage | n | vs nominal |
| --- | --- | --- | --- |
| honest errors vs `calibration/residuals.json` | 80.2% | 126 | -9.8% |

_(The served table is sized on autonomous-engine LOO residuals; applying it to researched errors is the faithful 'what interval does a reader see at this density' check. Its ~80% target is the honest calibration story; the resid_sd band is not.)_

## Top 10 WA misses — honest variant

| pos | title | genre | pool | actual | pred | signed err | analog | nA/nG |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 82 | The Last Shadow | Science Fiction (Har | 81 | 2.30 | 6.21 | 3.91 | author | 6/10 |
| 32 | Mistborn: Secret History | Literary Fantasy | 31 | 4.40 | 7.62 | 3.23 | author | 8/2 |
| 64 | The Stone of Farewell | Epic Fantasy | 63 | 4.54 | 7.40 | 2.86 | author | 1/28 |
| 68 | Station 11 | Speculative Literary | 67 | 5.15 | 7.83 | 2.68 | genre | 0/1 |
| 11 | Martin Eden | Literary Fiction | 10 | 5.60 | 7.88 | 2.29 | genre | 0/1 |
| 12 | Ironweed | Literary Fiction | 11 | 4.78 | 6.90 | 2.12 | genre | 0/2 |
| 55 | The Neverending Story | Literary Fantasy | 54 | 9.33 | 7.40 | -1.93 | genre | 0/7 |
| 35 | Shadows for Silence | Literary Fantasy | 34 | 5.07 | 6.90 | 1.83 | author | 11/5 |
| 115 | The Fifth Season | Science Fiction (Sof | 114 | 6.90 | 8.70 | 1.80 | genre | 0/15 |
| 15 | The Idiot | Russian Literature | 14 | 8.52 | 6.78 | -1.74 | author | 1/1 |

## Raw → corrected: where the correction helps / hurts

- Genres where the walk-forward correction **beats raw**: 9 (best: Literary Fiction, Speculative Literary Fiction, Science Fantasy).
- Genres where it **hurts vs raw**: 3 (Russian Literature, Literary Fantasy, Cyberpunk).
- Overall, honest correction changes WA MAE by **-0.168** vs raw (negative = correction helps).

## Reconciliation vs delta_log  (genuine pre-read predictions; informational)

| title | logged | historical pred | harness honest | harness leaky | actual | status |
| --- | --- | --- | --- | --- | --- | --- |
| The Republic of Thieves | 2026-06-30 | 7.51 | 6.94 | 6.93 | 6.82 | evaluated (pos 128) |
| The Wise Man's Fear | 2026-07-04 | 6.73 |   -   |   -   | 5.00 | not in current library |
| The Rise of Endymion | 2026-07-07 | 7.91 | 7.80 | 7.80 | 7.73 | evaluated (pos 129) |
| The Obelisk Gate | 2026-07-11 | 6.64 | 7.05 | 7.04 | 5.84 | evaluated (pos 127) |

Differences reflect engine/model drift between when each book was really predicted and today's cached-vector re-prediction — expected, not a failure. Rows marked _not in current library_ were predicted + rated historically but are absent from today's `books` table (removed / recategorised), so the harness has no fold for them.

