# Series-Trend Features — Walk-Forward Evaluation

Engine `sha256:6698c2df57591eb5` · 113 folds (burn-in 15), 83 in a real series · zero-API, read-only, deterministic.

Baseline = **honest** (walk-forward, `series_mode=None`) — reproduces `walkforward.py`'s honest MAE exactly. Each `+variant` is the SAME pipeline on the SAME past-only pool with only the series nudge added (`series_signal.py`): **level** = series mean · **trajectory** = last volume + slope · **both** = OLS line at the target ordinal. Combination weight `n/(n+K_SERIES)`, K_SERIES=2.0 (pre-registered, not tuned to this set).

## Overall WA MAE (all folds — diluted by standalones/first volumes)

| variant | WA MAE | Δ vs honest |
| --- | --- | --- |
| honest | 0.631 | — |
| honest+level | 0.635 | 0.003 |
| honest+trajectory | 0.676 | 0.044 |
| honest+both | 0.660 | 0.028 |

_Most folds are standalones or first volumes where the feature is a no-op, so the global number barely moves. The active-subset view below is the real test._

## WA MAE on the ACTIVE subset (series books with ≥N prior in-series reads)

| subset | n | honest | +level | +trajectory | +both | Δ both−honest |
| --- | --- | --- | --- | --- | --- | --- |
| ≥1 prior | 66 | 0.536 | 0.541 | 0.611 | 0.584 | 0.049 |
| ≥2 prior | 50 | 0.560 | 0.554 | 0.647 | 0.611 | 0.051 |
| ≥3 prior | 35 | 0.449 | 0.445 | 0.532 | 0.481 | 0.032 |

## Paired effect on the active subset (≥1 prior) — per-fold vs honest

| variant | n | helped | hurt | mean |err| reduction |
| --- | --- | --- | --- | --- |
| honest+level | 66 | 27 | 39 | -0.0057 |
| honest+trajectory | 66 | 26 | 40 | -0.0758 |
| honest+both | 66 | 27 | 39 | -0.0487 |

_Positive reduction = the variant beats honest. helped/hurt counts how many active folds each way._

## WA MAE by genre — active subset (≥1 prior)

| genre | n | honest | +level | +trajectory | +both |
| --- | --- | --- | --- | --- | --- |
| Epic Fantasy | 38 | 0.521 | 0.453 | 0.563 | 0.534 |
| Science Fiction (Soft) | 14 | 0.343 | 0.451 | 0.551 | 0.532 |
| Science Fantasy | 9 | 0.476 | 0.443 | 0.401 | 0.373 |
| Science Fiction (Hard) | 5 | 1.297 | 1.646 | 1.529 | 1.495 |

## Per-series effect — where the feature helps / hurts (mean |err| reduction vs honest)

**honest+level** — helps most: Ready Player One (+0.542, n=1), Mistborn Era 1 (+0.352, n=2), Memory, Sorrow, Thorn (+0.270, n=2), The First Law (+0.187, n=2), The Stormlight Archive (+0.131, n=4), Dungeon Crawler Carl (+0.059, n=7).
  hurts most: Hyperion Cantos (-0.589, n=2), Ender's Shadow (-0.445, n=2), Ender's Game (-0.284, n=3), The Hierarchy (-0.147, n=1), Gentleman Bastards (-0.127, n=1), The Burning (-0.119, n=1).

**honest+trajectory** — helps most: Ready Player One (+0.542, n=1), Mistborn Era 1 (+0.525, n=2), The First Law (+0.240, n=2), Dungeon Crawler Carl (+0.173, n=7), The Stormlight Archive (+0.151, n=4), Lord of the Rings (+0.013, n=2).
  hurts most: Hyperion Cantos (-0.511, n=2), Red Rising (-0.300, n=5), Thrawn Trilogy (-0.272, n=2), Ender's Game (-0.271, n=3), Gentleman Bastards (-0.266, n=1), Memory, Sorrow, Thorn (-0.232, n=2).

**honest+both** — helps most: Ready Player One (+0.542, n=1), Mistborn Era 1 (+0.525, n=2), The First Law (+0.240, n=2), Dungeon Crawler Carl (+0.210, n=7), The Stormlight Archive (+0.152, n=4), Lord of the Rings (+0.013, n=2).
  hurts most: Hyperion Cantos (-0.450, n=2), Thrawn Trilogy (-0.272, n=2), Gentleman Bastards (-0.266, n=1), Red Rising (-0.237, n=5), Memory, Sorrow, Thorn (-0.232, n=2), Ender's Game (-0.213, n=3).

## Interpretation — why the mean-pull nets to no gain

The series signal is a pull toward the series' own level/trend. It **helps** where the author-blended base under-shot a strong series — Rhythm of War (The Stormlight Archive): honest 7.01 → 7.92, actual 8.47; Dust of Dreams (Malazan: Book of the Fallen): honest 8.05 → 8.78, actual 8.97; Crossroads of Twilight (The Wheel of Time): honest 6.55 → 7.85, actual 7.54; The Shadow Rising (The Wheel of Time): honest 9.35 → 8.47, actual 8.57. It **hurts** where a specific volume broke from the series norm, or where honest was already accurate and the mean-pull added noise — Endymion (Hyperion Cantos): honest 7.73 → 8.43, actual 7.57; Wind and Truth (The Stormlight Archive): honest 7.77 → 8.26, actual 6.94; The Rise of Endymion (Hyperion Cantos): honest 7.81 → 8.29, actual 7.73; Children of the Mind (Ender's Game): honest 7.46 → 7.94, actual 6.98.

- **Epic Fantasy is the one bright spot** (the brief's expected home for series depth): level improves it 0.521 → 0.453 on n=38 — but the identical mechanism *hurts* the SF series (Ender's Game/Shadow, Hyperion), so the all-genre active subset nets flat. Acting on the EF slice alone would be fitting to this test set.

- **For single-series authors (Malazan/Erikson, WoT/Jordan) the series level *duplicates* the author mean** — they wrote only that series here, so the level target equals the author deviation the correction already uses (median series-vs-author level gap 0.00 WA, Phase 0.3). The pull still *moves* these predictions toward that flat mean, but as pure regression-to-the-mean it helps and hurts about equally and nets to zero per series. The only genuinely incremental cases are multi-series authors (Sanderson), where the series level ≠ the author mean.

- **Trajectory is worse than level**, not better: extrapolating a 2–4 point WA trend adds variance rather than signal at this library size — series volumes swing around their trend more than the slope predicts.

## Calibration — served conformal coverage (target 80%)

Does the point-estimate nudge wreck the served interval? Bucket each fold by its author-analog count and check the variant's WA error against that bucket's conformal half-width (`calibration/residuals.json`).

| variant | served coverage | n |
| --- | --- | --- |
| honest | 81.4% | 113 |
| honest+level | 81.4% | 113 |
| honest+trajectory | 79.6% | 113 |
| honest+both | 82.3% | 113 |

## Ship / no-ship decision

| variant | honest (≥1) | variant (≥1) | Δ MAE | coverage | decision |
| --- | --- | --- | --- | --- | --- |
| honest+level | 0.536 | 0.541 | 0.006 | 81.4% | no-ship |
| honest+trajectory | 0.536 | 0.611 | 0.076 | 79.6% | no-ship |
| honest+both | 0.536 | 0.584 | 0.049 | 82.3% | no-ship |

**Recommendation: SHIP NOTHING.** No variant improves honest WA MAE on its active subset. This confirms the Phase 0.3 hypothesis: the series *level* is already captured by the author pool (median series-vs-author level gap 0.00 WA), and the *trajectory* does not beat the running mean at this library size — the actuals are too noisy around the trend. A valid, useful negative result: keep the eval as the record and change nothing served.

**Most promising future lead** (do NOT act on it from this run — that would be fitting to the test): the series *level* concentrates its benefit on Epic Fantasy / multi-series authors. A genre- or multi-series-gated level, with K_SERIES chosen by proper *nested* cross-validation and re-checked as the library grows, is the right way to test that — not by slicing this walk-forward set post hoc.

