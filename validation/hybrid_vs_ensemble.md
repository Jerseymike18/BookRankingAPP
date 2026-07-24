# Ensemble vs the LIVE hybrid — honest ship comparison

The live predict default is the per-component **hybrid** (6 grounded + 8 memory), NOT memory-only. A ship must beat the hybrid. Correction refit per fold on each variant. Zero API cost.

Live grounded components: ['Depth', 'Depth2', 'Ending', 'Insights', 'Integration', 'Originality']

## Split: time  (n=116)

| variant | WA MAE | Spearman ρ | Kendall τ |
| --- | --- | --- | --- |
| memory | 0.628 | 0.690 | 0.505 |
| hybrid | 0.582 | 0.747 | 0.551 |
| ens25 | 0.594 | 0.728 | 0.538 |
| ens50 | 0.571 | 0.762 | 0.575 |

- hybrid − memory ΔMAE: **-0.046** [-0.082, -0.008]  (hybrid better)
- **ens25 − hybrid** ΔMAE: **+0.012** [-0.018, +0.041]  ❌ not significant vs hybrid
- **ens50 − hybrid** ΔMAE: **-0.012** [-0.040, +0.016]  ❌ not significant vs hybrid

## Split: author  (n=110)

| variant | WA MAE | Spearman ρ | Kendall τ |
| --- | --- | --- | --- |
| memory | 0.859 | 0.369 | 0.252 |
| hybrid | 0.820 | 0.466 | 0.310 |
| ens25 | 0.826 | 0.426 | 0.292 |
| ens50 | 0.809 | 0.511 | 0.350 |

- hybrid − memory ΔMAE: **-0.039** [-0.083, +0.004]  (not significant)
- **ens25 − hybrid** ΔMAE: **+0.006** [-0.028, +0.042]  ❌ not significant vs hybrid
- **ens50 − hybrid** ΔMAE: **-0.011** [-0.041, +0.019]  ❌ not significant vs hybrid

## Split: series  (n=114)

| variant | WA MAE | Spearman ρ | Kendall τ |
| --- | --- | --- | --- |
| memory | 0.817 | 0.461 | 0.314 |
| hybrid | 0.786 | 0.536 | 0.354 |
| ens25 | 0.788 | 0.512 | 0.351 |
| ens50 | 0.787 | 0.542 | 0.369 |

- hybrid − memory ΔMAE: **-0.032** [-0.074, +0.009]  (not significant)
- **ens25 − hybrid** ΔMAE: **+0.002** [-0.030, +0.036]  ❌ not significant vs hybrid
- **ens50 − hybrid** ΔMAE: **+0.002** [-0.030, +0.033]  ❌ not significant vs hybrid

