# Brief 1 — Cold-start regime slice: TabPFN vs. engine by support-set size

Walk-forward, current engine, zero-API. N = books rated strictly before the target (== harness pool_size == position−1). WA MAE is the harness's unweighted mean of per-fold |error|. Two TabPFN v2 challengers (Apache, seed 42, CPU): **mirror** (14 LLM comps + genre code + causal author/genre prior mean & count — the engine's own information) and **metadata** (the prior bake-off's 8 causal metadata features, no LLM vector).

- **Authoritative (burn-in 15, N≥15, 114 folds):** engine 0.6346 · mirror 0.6939 · meta 0.7059 · naive 0.8951
- **Exploratory union (burn-in 3, N≥3, 126 folds):** engine 0.6644 · mirror 0.7274 · meta 0.8237 · naive 0.9493

## WA MAE by N-bucket

| N bucket | n | mean N | engine | TabPFN mirror | TabPFN metadata | naive |
| --- | --- | --- | --- | --- | --- | --- |
| 3-7 ⚠ | 5 | 5.0 | 0.6810 | 0.9095 | 2.9702 | 1.7711 |
| 8-14 ⚠ | 7 | 11.0 | 1.1380 | 1.1428 | 1.2094 | 1.2453 |
| 15-24 | 10 | 19.5 | 0.4344 | 0.9105 | 0.5787 | 0.5742 |
| 25-39 | 15 | 32.0 | 0.9816 | 0.9034 | 0.6812 | 1.1152 |
| 40-59 | 20 | 49.5 | 0.7081 | 0.7740 | 0.8528 | 0.9210 |
| 60-89 | 30 | 74.5 | 0.6441 | 0.6757 | 0.8279 | 1.0924 |
| 90-128 | 39 | 109.0 | 0.5074 | 0.5308 | 0.5788 | 0.7278 |

⚠ = fewer than 10 folds (small-sample; read the delta against the count).

## Crossover — does TabPFN beat the engine at low N?

**TabPFN mirror:** first bucket beating the engine at N = **25-39**.

| N bucket | n | mirror − engine (WA MAE; − = TabPFN better) |
| --- | --- | --- |
| 3-7 | 5 | +0.2286 |
| 8-14 | 7 | +0.0048 |
| 15-24 | 10 | +0.4761 |
| 25-39 | 15 | -0.0781 |
| 40-59 | 20 | +0.0659 |
| 60-89 | 30 | +0.0315 |
| 90-128 | 39 | +0.0234 |

**TabPFN meta:** first bucket beating the engine at N = **25-39**.

| N bucket | n | meta − engine (WA MAE; − = TabPFN better) |
| --- | --- | --- |
| 3-7 | 5 | +2.2893 |
| 8-14 | 7 | +0.0714 |
| 15-24 | 10 | +0.1444 |
| 25-39 | 15 | -0.3004 |
| 40-59 | 20 | +0.1447 |
| 60-89 | 30 | +0.1837 |
| 90-128 | 39 | +0.0713 |

## Read

The plot is `mae_vs_n.svg`; the table is `bucketed_table.csv`; per-prediction rows are `perpred_bi15.csv` (authoritative) and `perpred_bi03.csv` (adds N<15). A GO for a cold-start hybrid needs TabPFN winning by a meaningful, stable margin in low-N buckets with non-trivial counts — judge the deltas above against their n.

