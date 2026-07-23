# Cold-start regime slice + honest blend — TabPFN vs. the engine

Analysis branch `analysis/tabpfn-cold-start-regime`. Zero-API, read-only, deterministic.
Nothing here touches the served path, `predict_engine.py`, `books.db`, or DeltaTracker.
Both briefs end **NO-GO**; the engine stays as-is.

## What was run

A walk-forward runner (`experiments/tabpfn_regime.py`) that **duplicates** `walkforward.py`'s
fold construction + temporal order (does not edit it) and, for every prediction point, logs the
engine's honest WA next to two TabPFN v2 challengers and a naive baseline, tagged with
**N = books rated strictly before the target** (== harness `pool_size` == position − 1).

- **Champion** = the engine's own `research_predict.correct_and_predict` honest WA. Verified
  **byte-identical** to `walkforward.py` across all 114 folds (|Δ| = 0), including the low-N
  exploratory folds (== the authoritative `walkforward --burn-in 3`).
- **TabPFN mirror** = the apples-to-apples challenger: 14 raw LLM components + genre code +
  causal author/genre prior mean & count (the engine's own information; no word-count/series,
  which the honest baseline can't see).
- **TabPFN metadata** = the prior bake-off's 8 causal metadata features (no LLM vector),
  reproduced on current data (its Red Rising prediction reproduces the committed bake-off's
  6.794909 exactly).
- TabPFN v2 (Apache-2.0, token-free, `tabpfn==2.2.1`, checkpoint cached offline), seed 42, CPU,
  deterministic (`--check-determinism` → identical sha256).
- Two burn-ins: **15** (authoritative, N≥15, 114 folds) and **3** (exploratory, N≥3, 126 folds)
  to reach genuinely-cold N. bi03 ⊃ bi15 and they agree byte-for-byte on the overlap.

## Brief 1 — does TabPFN win at low N?  **NO.**

Overall (N≥15): engine **0.6346** · mirror 0.6939 · meta 0.7059 · naive 0.8951 — both
challengers lose overall, as before. Per bucket (`regime_report.md`, plot `mae_vs_n.svg`):
**the engine is strongest exactly where the brief hoped TabPFN would win.** At the coldest N the
engine dominates (N 15–24: engine 0.434 vs mirror 0.911; N 3–7: engine 0.681 vs meta 2.97). The
only bucket where TabPFN edges ahead is **N 25–39** — a *mid*-N, 15-fold pocket driven by a local
engine spike (0.98 vs its ~0.5 neighbours), not a cold-start regime. No crossover trend toward
low N. **NO-GO for a cold-start hybrid.**

## Brief 2 — does an honest blend beat engine-alone?  **NO.**

`blend = w·engine + (1−w)·TabPFN`, w chosen out-of-fold (LOFO + expanding-window). Details in
`blend_report.md`.

- **Mirror blend collapses to engine-alone** (w → 1.00): residual correlation 0.915 — it shares
  the LLM vector, so it makes the same mistakes.
- **Metadata blend looks promising then fails the significance test.** LOFO OOF 0.6056 vs engine
  0.6346 (−0.029), w very stable (~0.59). But the paired bootstrap 95% CI is
  **[−0.019, +0.083] (straddles 0)**, the sign test is **58 better / 56 worse (coin-flip)**, and
  the gain is carried by ~3 classical-drama/epic outliers (Oresteia, Inferno, Rosencrantz) while
  the blend *hurts* books the engine already nails. Same outlier-driven pattern as the original
  bake-off. Not distinguishable from noise. **NO-GO.**
- **N-adaptive blend:** premise not met (no low-N TabPFN advantage); the honest optimum leans on
  the engine in both regimes.

## Why (one line)

The engine isn't a naive shrinker — it recenters a near-complete LLM vector with the right
inductive bias, and that bias matters *most* when data is thin. TabPFN needs data to learn and
flounders cold; where its metadata errors do decorrelate, the decorrelation isn't broad or stable
enough to survive honest weight selection. Consistent with the prior bake-off and cold-start-prior
NO-GOs.

## Files

| file | what |
|---|---|
| `../../experiments/tabpfn_regime.py` | per-prediction runner (duplicates walkforward; 2 challengers) |
| `../../experiments/tabpfn_regime_report.py` | Brief 1 + Brief 2 aggregation, stats, plot |
| `perpred_bi15.csv` / `.jsonl` | authoritative per-prediction table (N≥15, 114 folds) |
| `perpred_bi03.csv` / `.jsonl` | exploratory per-prediction table (N≥3, 126 folds) |
| `bucketed_table.csv` | WA MAE by N-bucket, all four predictors |
| `mae_vs_n.svg` | WA-MAE-vs-N plot (y-axis capped at 1.30; off-scale points annotated) |
| `regime_report.md` | Brief 1 report (buckets + crossover) |
| `blend_report.md` | Brief 2 report (decorrelation + honest blend + robustness) |
| `bi15/`, `bi03/` | authoritative `walkforward.py` folds (champion cross-check anchors) |

Reproduce: `.venv-tabpfn/bin/python experiments/tabpfn_regime.py --burn-in 15 --validate-champion
validation/regime/bi15/walkforward_folds.jsonl` then `… experiments/tabpfn_regime_report.py`.
