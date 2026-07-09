# ≥3-Prior Subset Probe — Real Regime Effect, or Slicing Artifact?

Source `bakeoff_predictions.jsonl` (113 folds) · engine `sha256:72a3d8cdfd15dba9` · bootstrap seed 42 × 10000 · measurement-only, read-only, deterministic. **Builds no predictor.**

## Framing

The +0.040 WA MAE ≥3 advantage was found by post-hoc slicing. Post-hoc subset selection inflates false positives and a tail slice is *noisier*, not cleaner, so one ≥3 cut clearing a CI is insufficient. The pre-committed rule below demands **replication across tail thresholds** and a **sustained crossover** in the error curve. A NO-GO is an expected, legitimate outcome.

## Phase 0 — reconciliation (gate before inference)

| quantity | recomputed from file | committed table | match |
| --- | --- | --- | --- |
| champion overall MAE | 0.6315 | 0.6315 | ✅ |
| champion ≥3 MAE | 0.6169 | 0.617 | ✅ |
| challenger ≥3 MAE | 0.5768 | 0.577 | ✅ |

_WA MAE weighting = plain unweighted mean of per-book absolute WA errors (no per-book weighting), identical to the harness._

## Phase 1 — subset sizing

| prior-count bin | n |
| --- | --- |
| 0 | 33 |
| 1 | 17 |
| 2 | 14 |
| 3 | 10 |
| 4 | 8 |
| 5+ | 31 |

| cumulative | n |
| --- | --- |
| ≥1 | 80 |
| ≥2 | 63 |
| ≥3 | 49 |
| ≥4 | 39 |
| ≥5 | 31 |

**Sizing gate:** n(≥3) = **49** ≥ 30 → not auto-killed; proceed.

## Phase 2 — stability across the tail (replication test)

| subset | n | champion | challenger | delta (ch−cl) | paired 95% CI | point winner |
| --- | --- | --- | --- | --- | --- | --- |
| ≥1 | 80 | 0.579 | 0.634 | -0.054 | [-0.168, +0.064] | champion |
| ≥2 | 63 | 0.610 | 0.613 | -0.003 | [-0.130, +0.131] | champion |
| ≥3 | 49 | 0.617 | 0.577 | +0.040 | [-0.102, +0.183] | challenger |
| ≥4 | 39 | 0.696 | 0.633 | +0.063 | [-0.110, +0.241] | challenger |
| ≥5 | 31 | 0.756 | 0.643 | +0.113 | [-0.089, +0.319] | challenger |
| ≥6 ⚠ n<30 | 23 | 0.951 | 0.755 | +0.196 | [-0.064, +0.455] | challenger |

_Positive delta / CI = challenger better. **Every** CI straddles zero — including ≥3 — so no threshold gives a statistically clean challenger advantage. The point estimate does trend upward into the tail, but Phase 3 shows why that is misleading._

## Phase 3 — error curve vs prior-count (mechanism check)

| prior-count bin | n | champion MAE | challenger MAE | per-bin diff (ch−cl) |
| --- | --- | --- | --- | --- |
| 0 | 33 | 0.758 | 0.856 | -0.097 |
| 1 | 17 | 0.466 | 0.710 | -0.244 |
| 2 | 14 | 0.585 | 0.739 | -0.154 |
| 3 | 10 | 0.309 | 0.357 | -0.048 |
| 4 | 8 | 0.462 | 0.594 | -0.133 |
| 5+ | 31 | 0.756 | 0.643 | +0.113 |

_Per-bin (non-cumulative). First challenger-favoured bin: **5+**. Champion leads every resolved single bin (0–4); only the **pooled** 5+ bin flips positive._

**Fine tail split (n_author ≥ 5) — is the 5+ flip a few outliers?**

| n_author | n | champion | challenger | diff (ch−cl) |
| --- | --- | --- | --- | --- |
| 5 | 8 | 0.196 | 0.320 | -0.124 |
| 6 | 6 | 1.109 | 1.216 | -0.107 |
| 7 | 4 | 0.502 | 0.506 | -0.004 |
| 8 | 3 | 1.494 | 0.903 | +0.591 |
| 9 | 3 | 0.405 | 0.409 | -0.005 |
| 10 | 2 | 1.055 | 0.400 | +0.655 |
| 11 | 2 | 1.025 | 0.687 | +0.338 |
| 12 | 1 | 1.463 | 0.074 | +1.389 |
| 13 | 1 | 1.069 | 0.211 | +0.858 |
| 14 | 1 | 0.832 | 1.658 | -0.826 |

_The 5+ flip is driven by a handful of individual high-prior books (1–3 books per cell) where the champion made large errors; at least one high-prior cell reverses. This is a single noisy pooled bin, not a sustained regime._

**Decomposing the ≥3 bucket — where does the +0.040 come from?**

| sub-bucket | n | champion | challenger | delta (ch−cl) |
| --- | --- | --- | --- | --- |
| 3–4 prior | 18 | 0.377 | 0.463 | -0.086 |
| 5+ prior | 31 | 0.756 | 0.643 | +0.113 |

_The 3–4-prior regime — the books that first enter the ≥3 cut — favours the **champion** (-0.086). The entire cumulative ≥3 advantage is the 5+ tail (+0.113) leaking across the threshold. The ≥3 line is not a 3-prior effect._

## Pre-committed verdict

| condition | result | detail |
| --- | --- | --- |
| 1. ≥3 bucket n ≥ ~25-30 | ✅ pass | n(≥3) = 49 (floor 30) |
| 2. paired CI at ≥3 excludes 0 (challenger-favoured) | ❌ FAIL | CI = [-0.10225, 0.18267] |
| 3. advantage replicates at ≥4 (not reversing) | ✅ pass | delta(≥4) = +0.062786 |
| 4. sustained crossover (bins 3 AND 4 favour challenger) | ❌ FAIL | per-bin diff: bin3 = -0.048203, bin4 = -0.132896 |

**BOTTOM LINE: NO-GO — thread closed.** Binding reasons: 2. paired CI at ≥3 excludes 0 (challenger-favoured); 4. sustained crossover (bins 3 AND 4 favour challenger). The ≥3 advantage does not survive its own stability check — its paired CI straddles zero and the per-bin curve shows the ‘advantage’ is the outlier-driven 5+ tail, not a 3-prior crossover (the 3–4-prior books favour the champion). Consistent with the bake-off's overall CI straddling zero: this is noise from post-hoc slicing, not a regime effect. No scoped variant is warranted.

