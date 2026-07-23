# Two-method research ensemble — full evaluation (Phase: new signals)

Ensemble = (1−w)·richer[model-knowledge] + w·web_grounded[web-search], both already cached (zero API cost). Full honest walk-forward with the correction REFIT on the ensembled pairs per fold. **w=0 is today's baseline; w=1 is web-grounded only.** Lower MAE / higher ρ,τ better.

## Split: time  (n=116)

| weight w | WA MAE | Δ vs base [95% CI] | Spearman ρ | Kendall τ |
| --- | --- | --- | --- | --- |
| 0.00 | 0.628 | — (baseline) | 0.690 | 0.505 |
| 0.25 | 0.594 | -0.034 [-0.051, -0.018] ⭐ | 0.728 | 0.538 |
| 0.50 | 0.571 | -0.058 [-0.088, -0.026] ⭐ | 0.762 | 0.575 |
| 0.75 | 0.569 | -0.059 [-0.105, -0.013] ⭐ | 0.769 | 0.582 |
| 1.00 | 0.576 | -0.053 [-0.111, +0.004] | 0.772 | 0.582 |

## Split: author  (n=110)

| weight w | WA MAE | Δ vs base [95% CI] | Spearman ρ | Kendall τ |
| --- | --- | --- | --- | --- |
| 0.00 | 0.859 | — (baseline) | 0.369 | 0.252 |
| 0.25 | 0.826 | -0.033 [-0.050, -0.017] ⭐ | 0.426 | 0.292 |
| 0.50 | 0.809 | -0.050 [-0.088, -0.013] ⭐ | 0.511 | 0.350 |
| 0.75 | 0.800 | -0.059 [-0.116, -0.004] ⭐ | 0.535 | 0.370 |
| 1.00 | 0.802 | -0.057 [-0.128, +0.013] | 0.534 | 0.366 |

## Split: series  (n=114)

| weight w | WA MAE | Δ vs base [95% CI] | Spearman ρ | Kendall τ |
| --- | --- | --- | --- | --- |
| 0.00 | 0.817 | — (baseline) | 0.461 | 0.314 |
| 0.25 | 0.788 | -0.029 [-0.045, -0.013] ⭐ | 0.512 | 0.351 |
| 0.50 | 0.787 | -0.030 [-0.063, +0.005] | 0.542 | 0.369 |
| 0.75 | 0.794 | -0.024 [-0.073, +0.028] | 0.542 | 0.367 |
| 1.00 | 0.805 | -0.013 [-0.077, +0.054] | 0.537 | 0.358 |

## Per-component estimation MAE — baseline (w=0) vs ensemble (w=0.5), time split

| component | base MAE | ensemble MAE | Δ |
| --- | --- | --- | --- |
| Ending | 1.200 | 1.053 | -0.146 |
| Emotional Impact | 1.056 | 0.954 | -0.102 |
| Plot | 0.844 | 0.766 | -0.078 |
| Prose | 0.657 | 0.600 | -0.058 |
| Action | 0.832 | 0.786 | -0.046 |
| Depth | 0.808 | 0.765 | -0.043 |
| Motivations | 0.834 | 0.795 | -0.039 |
| Narration | 0.869 | 0.834 | -0.035 |
| Thought-Provokingness | 0.812 | 0.781 | -0.031 |
| Originality | 0.785 | 0.757 | -0.028 |
| Insights | 0.709 | 0.688 | -0.020 |
| Entertainment | 0.830 | 0.811 | -0.019 |
| Integration | 0.831 | 0.821 | -0.010 |
| Depth2 | 0.830 | 0.831 | +0.001 |

_⭐ = ensemble beats baseline with the whole ΔMAE 95% CI below 0. The cold-start author/series splits are the ones that matter for a ship decision._

