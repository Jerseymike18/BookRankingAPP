# Multi-sample research averaging — free K=2 probe (Phase: new signals)

Average the two independent grounded caches (richer + web_grounded) and run the honest walk-forward pipeline vs the single-pass baseline. **Negative ΔMAE with CI below 0 = the averaging signal is real** (and K>2 fresh samples would help more).

| split | n | base MAE | avg2 MAE | ΔMAE [95% CI] | ρ base→avg2 |
| --- | --- | --- | --- | --- | --- |
| time | 116 | 0.628 | 0.576 | -0.052 [-0.081, -0.023] | 0.740 |
| author | 110 | 0.859 | 0.832 | -0.027 [-0.059, +0.003] | 0.413 |
| series | 114 | 0.817 | 0.795 | -0.023 [-0.052, +0.007] | 0.513 |

_131/131 rated books have both passes. This is only K=2 (and the two passes use different prompts, so it understates what K clean same-prompt samples would give). A real win here justifies a fresh multi-sample research run targeted at the high-disagreement / high-headroom components (Ending, Depth, Plot)._

