# Error Decomposition (Phase 2.2)

Honest walk-forward folds (time split), n=116. Splits total WA error into component-estimation vs aggregation.

## The split

| source | WA MAE | share of total |
| --- | --- | --- |
| **component-estimation** (predicting the 14 components) | 0.6282 | 100.0% |
| **aggregation** (true components → overall score) | 0.0000 | 0.0% |
| total (full pipeline) | 0.6282 | 100% |

Max single-book aggregation error: 5.00e-07 (floating-point only).

**Aggregation is structurally exact.** WA is a fixed linear roll-up of the components (`WA = Σ_c compₐ · eff_weightₐ`), and the prediction path uses the very same roll-up (`_wa_from_components`) that *defines* the actual WA — so pushing true components through it reproduces actual WA to floating point. **≈100% of the engine's error is component-estimation**; there is essentially nothing to win by re-learning the aggregation.

→ **Gate B routes to Branch A** (component-estimation). Branch B (ridge / GBT to re-learn weights) is near-degenerate against a target that is itself a fixed weighted sum of those components (the R²≈0.99 fact, confirmed here).

## Which components drive the WA error  (Branch-A targets)

Mean |estimation error| per component, and its mean contribution to WA error (`eff_weight · |pred−actual|`). Sorted by WA-error contribution.

| component | est MAE | mean WA-error contribution |
| --- | --- | --- |
| Plot | 0.844 | 0.1154 |
| Ending | 1.200 | 0.0996 |
| Depth | 0.808 | 0.0951 |
| Thought-Provokingness | 0.812 | 0.0947 |
| Emotional Impact | 1.056 | 0.0881 |
| Insights | 0.709 | 0.0638 |
| Entertainment | 0.830 | 0.0625 |
| Prose | 0.657 | 0.0506 |
| Depth2 *(WB)* | 0.830 | 0.0472 |
| Action | 0.832 | 0.0460 |
| Narration | 0.869 | 0.0420 |
| Motivations | 0.834 | 0.0412 |
| Integration *(WB)* | 0.831 | 0.0184 |
| Originality *(WB)* | 0.785 | 0.0093 |

