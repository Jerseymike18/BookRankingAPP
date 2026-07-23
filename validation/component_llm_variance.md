# Component LLM variance vs headroom (new-signal diagnostic)

131/131 rated books have two independent grounded passes. **inter-pass |Δ|** = mean disagreement between the two passes (a proxy for LLM sampling noise); **est-MAE** = engine estimation error (2.2); **floor** = test-retest noise (2.1); **reducible** = est−floor.

| component | inter-pass \|Δ\| | est-MAE | floor | reducible |
| --- | --- | --- | --- | --- |
| Ending | 0.622 | 1.200 | 0.665 | +0.535 |
| Plot | 0.437 | 0.844 | 0.506 | +0.338 |
| Depth | 0.545 | 0.808 | 0.485 | +0.323 |
| Entertainment | 0.360 | 0.830 | 0.512 | +0.318 |
| Action | 0.468 | 0.832 | 0.554 | +0.278 |
| Thought-Provokingness | 0.373 | 0.812 | 0.665 | +0.147 |
| Originality | 0.427 | 0.785 | 0.648 | +0.137 |
| Motivations | 0.558 | 0.834 | 0.715 | +0.119 |
| Depth2 | 0.444 | 0.830 | 0.752 | +0.078 |
| Narration | 0.457 | 0.869 | 0.792 | +0.077 |
| Emotional Impact | 0.516 | 1.056 | 0.988 | +0.068 |
| Insights | 0.403 | 0.709 | 0.646 | +0.063 |
| Integration | 0.575 | 0.831 | 0.862 | -0.031 |
| Prose | 0.430 | 0.657 | 0.708 | -0.051 |

**Read:** components high on BOTH inter-pass |Δ| and reducible headroom (Ending 0.62/+0.53, Depth 0.55/+0.32, Plot 0.44/+0.34) carry error that is largely LLM noise ⇒ multi-sample averaging targets them. Emotional Impact has high disagreement but ~0 headroom (floor-limited) — averaging can't help its WA.

