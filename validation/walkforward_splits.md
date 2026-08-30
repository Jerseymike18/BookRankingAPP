# Walk-Forward — Split-Mode Baseline (Phase 1)

Reading order = DB `read_seq` · burn-in 15 · engine `sha256:9d2b7c25bb42314b` · git `2a43f715cde3`.

All modes keep the walk-forward past-only pool; grouped modes additionally drop same-author / same-series earlier books. **raw** is pool-independent (WA identical in every mode) and **leaky** is the fixed full-library reference (identical in every mode) — the **honest** rows carry the signal.

## Live-served accuracy (hybrid) by split mode  — THE REAL BASELINE

The app serves the hybrid vector (memory + web-grounded overrides) after the background grounded-upgrade, so this is what a reader actually gets. `honest` below is the memory-only pre-refine state (what earlier reports called the baseline; it understates live accuracy).

| split mode | n folds | hybrid WA MAE | ρ | τ | honest (memory) MAE |
| --- | --- | --- | --- | --- | --- |
| time | 125 | 0.550 | 0.757 | 0.571 | 0.587 |
| author | 119 | 0.745 | 0.519 | 0.363 | 0.810 |
| series | 123 | 0.734 | 0.567 | 0.398 | 0.775 |

## All variants × split mode  (WA MAE / ρ / τ)

| variant | split mode | n | WA MAE | ρ | τ |
| --- | --- | --- | --- | --- | --- |
| raw | time | 125 | 0.783 | 0.453 | 0.307 |
| raw | author | 119 | 0.795 | 0.442 | 0.301 |
| raw | series | 123 | 0.787 | 0.456 | 0.310 |
| honest | time | 125 | 0.587 | 0.716 | 0.530 |
| honest | author | 119 | 0.810 | 0.413 | 0.288 |
| honest | series | 123 | 0.775 | 0.494 | 0.339 |
| leaky | time | 125 | 0.540 | 0.806 | 0.611 |
| leaky | author | 119 | 0.535 | 0.817 | 0.623 |
| leaky | series | 123 | 0.541 | 0.806 | 0.612 |
| hybrid | time | 125 | 0.550 | 0.757 | 0.571 |
| hybrid | author | 119 | 0.745 | 0.519 | 0.363 |
| hybrid | series | 123 | 0.734 | 0.567 | 0.398 |

## Honest variant on the common fold subset  (n=119; identical books scored in every mode)

| split mode | n | WA MAE | Spearman ρ | Kendall τ |
| --- | --- | --- | --- | --- |
| time | 119 | 0.592 | 0.716 | 0.531 |
| author | 119 | 0.810 | 0.413 | 0.288 |
| series | 119 | 0.779 | 0.491 | 0.338 |

_The common-subset table is the clean apples-to-apples read: identical books, only the training-pool grouping differs._
