# Feature-Adaptive Prediction Intervals (Phase 5)

Walk-forward, 80% level (α=0.2). Calibrated on PAST folds only (warm-up 30; before that, global past band). **Coverage must hold ≈80%; only then does smaller mean width count.**

| split | method | coverage | mean width | median width | n |
| --- | --- | --- | --- | --- | --- |
| time | current | 81.9% | 2.080 | 1.842 | 116 |
| time | wf_bucket | 87.8% | 2.359 | 2.475 | 115 |
| time | wf_global | 84.3% | 2.284 | 2.255 | 115 |
| time | hetero | 77.4% | 2.096 | 1.892 | 115 |
| time | cqr | 75.7% | 2.514 | 2.475 | 115 |
| | | | | | |
| author | current | 81.8% | 2.671 | 2.671 | 110 |
| author | wf_bucket | 89.9% | 3.783 | 3.367 | 109 |
| author | wf_global | 89.9% | 3.783 | 3.367 | 109 |
| author | hetero | 72.5% | 2.858 | 2.097 | 109 |
| author | cqr | 73.4% | 3.158 | 2.994 | 109 |
| | | | | | |
| series | current | 78.9% | 2.477 | 2.671 | 114 |
| series | wf_bucket | 82.3% | 3.029 | 2.943 | 113 |
| series | wf_global | 82.3% | 3.072 | 3.002 | 113 |
| series | hetero | 77.0% | 2.789 | 2.870 | 113 |
| series | cqr | 75.2% | 3.100 | 2.943 | 113 |
| | | | | | |

**Verdict — NO-GO.** The deployed density-bucketed band already holds ~80% coverage (time 81.9% / author 81.8% / series 78.9%) at the SMALLEST mean width. Neither the heteroscedastic model (5.1) nor CQR (5.2) beats it: both UNDER-cover (hetero 72–77%, cqr 73–76%) and are as-wide-or-wider. `n_author` (analog density) is the dominant uncertainty signal and the current band already uses it (bucketed); with only ~100 past-calibration points the extra features add noise, not adaptive tightening, and the small walk-forward calibration set under drift erodes coverage. The conformal band stays the interval authority, unchanged.

