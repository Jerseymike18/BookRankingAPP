# Brief 2 — Engine × TabPFN blend: does an honest blend beat engine-alone?

blend = w·engine + (1−w)·TabPFN (w = weight on the ENGINE). Same walk-forward folds as Brief 1; WA MAE unweighted. w chosen **out-of-fold** — leave-one-fold-out (LOFO) and expanding-window — so it is never scored on the folds that picked it. Headline is the authoritative N≥15 set (the brief's 0.63 baseline); the N≥3 union is the exploratory extension.

## Phase 1 — decorrelation diagnostic

Engine residual vs. TabPFN residual (signed-error) Pearson correlation. High positive → the two make the same mistakes → a blend can't help much.

Overall (N≥15): mirror **0.915** · meta **0.735**.

Per-bucket (N≥3 union):

| variant | overall | 3-7 | 8-14 | 15-24 | 25-39 | 40-59 | 60-89 | 90-128 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| mirror | 0.918 | 0.66 | 0.98 | 0.95 | 0.96 | 0.93 | 0.95 | 0.85 |
| meta | 0.697 | -0.06 | 0.89 | 0.61 | 0.72 | 0.79 | 0.79 | 0.67 |

## Phase 2 — honest fixed-weight blend

**Authoritative — N≥15** (114 folds)

| variant | engine (matched) | in-sample best (w) | LOFO OOF (Δ vs engine) | exp-window OOF (Δ vs matched) | LOFO w  p10–p50–p90 |
| --- | --- | --- | --- | --- | --- |
| mirror | 0.6346 | 0.6346 (w=1.00) | 0.6402 (+0.0057) | 0.6193 (-0.0000) | 0.99 – 1.00 – 1.00 |
| meta | 0.6346 | 0.6029 (w=0.59) | 0.6056 (-0.0289) | 0.6066 (-0.0126) | 0.58 – 0.59 – 0.59 |

**Exploratory — N≥3 union** (126 folds)

| variant | engine (matched) | in-sample best (w) | LOFO OOF (Δ vs engine) | exp-window OOF (Δ vs matched) | LOFO w  p10–p50–p90 |
| --- | --- | --- | --- | --- | --- |
| mirror | 0.6644 | 0.6644 (w=1.00) | 0.6657 (+0.0013) | 0.6521 (-0.0000) | 1.00 – 1.00 – 1.00 |
| meta | 0.6644 | 0.6569 (w=0.87) | 0.6637 (-0.0007) | 0.6447 (-0.0074) | 0.87 – 0.87 – 0.88 |

_w=1.00 means the honest search fell back to engine-alone. A wide p10–p90 = a fold-unstable weight (a red flag even if the mean improves). The matched engine baseline for exp-window is measured on the same later folds it scores._

## Phase 3 — N-adaptive blend

Premise (per the brief): only warranted if Brief 1 found a low-N TabPFN advantage. It did **not** — no N<25 bucket beats the engine. Running it anyway to confirm the honest optimum collapses toward engine-alone:

- **mirror:** LOFO N-adaptive OOF = 0.6759 vs engine 0.6644 (Δ +0.0115); median (t, w_low, w_high) = (28, 1.00, 0.55) — w's near 1.0 = lean on the engine in both regimes.
- **meta:** LOFO N-adaptive OOF = 0.6317 vs engine 0.6644 (Δ -0.0327); median (t, w_low, w_high) = (28, 1.00, 0.55) — w's near 1.0 = lean on the engine in both regimes.

## Robustness of the best honest blend (N≥15)

Fixed w=0.59 on the meta blend (the stable LOFO weight), paired against engine-alone over 114 folds:

- point Δ (blend − engine): **-0.0317**
- folds blend **better 58** / **worse 56** (≈ coin-flip if ~57/57)
- paired bootstrap 95% CI of mean |error| improvement: **[-0.0192, +0.0833]**
- Δ with the top-3 improving folds removed: **-0.0124** (if this collapses toward 0, a few outliers drove the gain)

## Decision gate

GO requires an honest blend beating the engine baseline out-of-fold by a margin that (a) is meaningful (≥0.02), (b) holds out-of-fold, and (c) doesn't rest on a fold-unstable weight. Best honest OOF blend (meta) = **0.6056** vs engine **0.6346** (Δ **-0.0289**), w stable at ~0.59. **VERDICT: NO-GO.** The point delta clears 0.02, BUT the paired bootstrap CI straddles zero and the sign test is a coin-flip — the improvement is a few classical-drama/epic outliers, not a broad, stable gain (the mirror blend, sharing the LLM vector, collapses to engine-alone). Not distinguishable from noise.

