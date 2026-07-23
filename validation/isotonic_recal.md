# Isotonic Recalibration (Phase 4.1)

Monotone map on the honest OOF predictions. Rank order is preserved by construction (MAE-only). **wf-isotonic is the authority; loo-isotonic is an upper-bound reference.** Warm-up: identity until 20 past pairs.

Calibration diagnostic: OLS `actual ~ slope·pred + intercept`. slope > 1 ⇒ predictions compressed toward the mean (under-dispersed); slope < 1 ⇒ over-dispersed (over-shoots the extremes). Either is a monotone miscalibration a recal map could in principle fix.

| split | n | slope | intc | base MAE | wf-iso Δ [95% CI] | wf-linear Δ [95% CI] | loo-iso Δ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| time | 116 | 1.044 | -0.491 | 0.628 | +0.106 [+0.057, +0.157] | +0.035 [+0.001, +0.068] | +0.088 |
| author | 110 | 0.754 | +1.775 | 0.859 | +0.023 [-0.045, +0.089] | +0.064 [-0.015, +0.141] | +0.037 |
| series | 114 | 0.938 | +0.428 | 0.817 | +0.036 [-0.018, +0.089] | +0.025 [-0.017, +0.070] | +0.063 |

_A SHIP needs a wf Δ CI entirely below 0. wf-linear (2 params) is the minimal monotone recal; if even it doesn't win, flexible isotonic overfitting is not the only reason — there is simply no monotone recal gain._

