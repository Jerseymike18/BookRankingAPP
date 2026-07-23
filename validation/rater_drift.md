# Rater Drift (Phase 2.3)

Mean **actual WA** (all rated books) and mean **model signed residual** (honest walk-forward, pred − actual) over time. Only 2 calendar years of ratings exist, so the month view (≈19 points) is the more informative trend.

## By year

| year | n | mean actual WA | n folds | mean signed residual |
| --- | --- | --- | --- | --- |
| 2025 | 73 | 7.510 | 58 | +0.192 |
| 2026 | 58 | 7.644 | 58 | +0.104 |

## By month

| month | n | mean actual WA | n folds | mean signed residual |
| --- | --- | --- | --- | --- |
| 2025-01 | 3 | 6.834 | 0 | - |
| 2025-02 | 5 | 7.193 | 0 | - |
| 2025-03 | 7 | 8.129 | 0 | - |
| 2025-04 | 7 | 7.458 | 7 | +0.217 |
| 2025-05 | 11 | 7.078 | 11 | +0.212 |
| 2025-06 | 14 | 7.948 | 14 | -0.072 |
| 2025-07 | 9 | 7.381 | 9 | +0.333 |
| 2025-08 | 5 | 6.718 | 5 | +0.848 |
| 2025-09 | 1 | 8.087 | 1 | +0.432 |
| 2025-10 | 1 | 7.485 | 1 | +0.840 |
| 2025-11 | 3 | 7.915 | 3 | -0.107 |
| 2025-12 | 7 | 7.740 | 7 | +0.015 |
| 2026-01 | 5 | 6.391 | 5 | +1.005 |
| 2026-02 | 3 | 8.128 | 3 | +0.202 |
| 2026-03 | 10 | 8.142 | 10 | -0.004 |
| 2026-04 | 14 | 7.627 | 14 | +0.178 |
| 2026-05 | 12 | 7.910 | 12 | -0.235 |
| 2026-06 | 10 | 7.505 | 10 | -0.030 |
| 2026-07 | 4 | 7.208 | 4 | +0.272 |

## Trend

- **Mean actual WA**: slope **+0.0181 WA/month** (≈ +0.217/year; total ≈ +0.325 over the span). Rising — he rates a little higher over time / picks better books.
- **Model signed residual**: slope **-0.0177/month** (≈ -0.212/year). Model increasingly under-predicts.
- Year-over-year mean WA change: **+0.134** (2025 7.51 → 2026 7.64).

_Caveat: 2 calendar years is a short base; treat the slope as indicative, not a fitted drift term. Whether to add a slow time term is a Branch-A option (Phase 3), gated on this being non-trivial._

