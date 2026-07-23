# Walk-Forward — Split-Mode Baseline (Phase 1)

Reading order = DB `read_seq` · burn-in 15 · engine `sha256:681f0e6a4fd83f53` · git `9d8640c025d1`.

All modes keep the walk-forward past-only pool; grouped modes additionally drop same-author / same-series earlier books. **raw** is pool-independent (WA identical in every mode) and **leaky** is the fixed full-library reference (identical in every mode) — the **honest** rows carry the signal.

## Honest variant by split mode  (the walk-forward baseline)

| split mode | n folds | WA MAE | Spearman ρ | Kendall τ |
| --- | --- | --- | --- | --- |
| time | 116 | 0.628 | 0.690 | 0.505 |
| author | 110 | 0.859 | 0.369 | 0.252 |
| series | 114 | 0.817 | 0.461 | 0.314 |

## All variants × split mode  (WA MAE / ρ / τ)

| variant | split mode | n | WA MAE | ρ | τ |
| --- | --- | --- | --- | --- | --- |
| raw | time | 116 | 0.826 | 0.437 | 0.296 |
| raw | author | 110 | 0.841 | 0.424 | 0.287 |
| raw | series | 114 | 0.832 | 0.439 | 0.299 |
| honest | time | 116 | 0.628 | 0.690 | 0.505 |
| honest | author | 110 | 0.859 | 0.369 | 0.252 |
| honest | series | 114 | 0.817 | 0.461 | 0.314 |
| leaky | time | 116 | 0.585 | 0.773 | 0.580 |
| leaky | author | 110 | 0.583 | 0.783 | 0.592 |
| leaky | series | 114 | 0.587 | 0.773 | 0.581 |

## Honest variant on the common fold subset  (n=110; identical books scored in every mode)

| split mode | n | WA MAE | Spearman ρ | Kendall τ |
| --- | --- | --- | --- | --- |
| time | 110 | 0.637 | 0.687 | 0.503 |
| author | 110 | 0.859 | 0.369 | 0.252 |
| series | 110 | 0.825 | 0.455 | 0.310 |

_The common-subset table is the clean apples-to-apples read: identical books, only the training-pool grouping differs._
