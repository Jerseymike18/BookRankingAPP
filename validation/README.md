# Walk-forward validation artifacts

This directory holds the outputs of `walkforward.py` — a **chronological backtest** of the
researched-prediction engine. For each rated fiction book it asks: *what would the engine have
predicted on the day I started this book, using only the books read before it?* That is the
honest **"what was knowable then"** accuracy baseline future engine features must beat, and the
raw dataset a future public track-record page will consume.

These files are **not** static-snapshot inputs — the publish export/hooks read only `books.db`,
so nothing here churns the public site.

## Regenerating

```
python3 walkforward.py                 # run all folds + write the report
python3 walkforward.py --report-only   # rebuild the report from an existing folds artifact
python3 walkforward.py --check-determinism   # assert two runs are byte-identical
python3 walkforward.py --burn-in 15    # min training-pool size before a fold is evaluated
```

Read-only and **zero-spend by construction**: the harness reads the richer-prompt cache
(`llm_scores_richer.json`) as a plain dict and blocks `anthropic.Anthropic`, so it can never
trigger an LLM call. `books.db` is opened read-only and never written (folds are in-memory
filters of the books DataFrame). Same DB + caches ⇒ byte-identical `…_folds.jsonl`.

## Files

| File | What it is |
| --- | --- |
| `walkforward_folds.jsonl` | One JSON object per evaluated fold, then one per skip. **Deterministic** (sorted keys, fixed rounding, no timestamps). The raw dataset. |
| `walkforward_meta.json` | Run provenance: git head, engine hash, burn-in, counts, the **leakage inventory**, caveats, active correction version. Carries a wall-clock timestamp (the only volatile field). |
| `walkforward_report.md` | Human-readable summary (see below). Timestamp-free ⇒ byte-identical on re-run at the same commit. |
| `walkforward_rolling_mae.json` | Per-fold WA abs-errors + trailing-window rolling MAE for each variant — the track-record-page series. |

### Fold record (`…_folds.jsonl`)

```jsonc
{
  "position": 16,                    // 1-based reading-order position
  "title": "...", "author": "...", "genre": "...",
  "series": "... | null", "series_number": 1, "year_read": 2025,
  "in_timeline": true,               // false = placed-last book (see Ordering)
  "pool_size": 15,                   // training books read strictly before this one
  "cache_key": "...",                // richer-cache key (== title)
  "actual_wa": 7.729,
  "actual_components": { "Plot": 8.1, ... },      // 14 components
  "variants": {
    "raw":    { ... }, "honest": { ... }, "leaky": { ... }
  }
}
```

Each variant object holds: `wa`, `components` (14 corrected/raw scores), `wa_signed_error`,
`wa_abs_error`, `component_signed_error`/`component_abs_error` (per component),
`ci_low`/`ci_high`/`ci_inside`, `resid_sd`, `rank`/`rank_total`, and the correction grounding
(`n_author`, `n_genre`, `analog_src`). A skip record is `{"skip": true, "position", "title",
"reason"}` with reason `BURN_IN` (pool below `--burn-in`) or `SKIPPED_NO_CACHE` /
`SKIPPED_NO_TIMELINE_MATCH` (defensive; empty under current data).

## The three variants

| Variant | Correction | Trained on | Answers |
| --- | --- | --- | --- |
| **raw** | none (research → WA) | — | "how good is the uncalibrated grounded research?" |
| **honest** | author+genre (+ smoothing) | **past-only pool** (books 1…t−1) | **"what was knowable then"** — the walk-forward baseline |
| **leaky** | author+genre (+ smoothing) | **full library** (today's config) | "how good is today's engine config" — *labeled leaky* |

`honest` is the number that matters. `leaky` is the same pipeline fit on the whole library
(so its correction saw future books) — the honest→leaky gap is the "leakage premium." `raw`
isolates how much the taste-correction adds.

## Reading the report

- **Overall WA MAE** — headline accuracy per variant vs a naive "predict the mean WA" baseline.
- **By genre / by year-read** — where the correction helps or hurts; later years have larger
  pools and predict better.
- **Rolling WA MAE** — the *"engine getting smarter as the library grew"* curve; the honest
  series starts noisy on thin early pools and converges toward `leaky` as the pool fills.
- **Component MAE (worst-first)** — which of the 14 components carry signal. Worldbuilding rows
  whose **actual** value is the `0.0` "no worldbuilding" sentinel (realist genres) are excluded,
  matching the engine's own training exclusion.
- **Interval coverage** — see the caveat below.
- **Top-10 misses** — largest honest |WA error| with analog source; qualitative feature fuel.
- **Reconciliation** — the harness's fold prediction vs the *real* historical `delta_log`
  prediction for books genuinely predicted before reading. Informational (different engine
  versions/models over time). A row can read *not in current library* if it was predicted +
  rated historically but has since been removed/recategorised from `books`.

## Known caveats

1. **Research-cache hindsight (accepted).** The 14-component research vector for each book
   comes from the cache, which was produced with knowledge of the book's post-publication
   reception. A walk-forward run cannot un-know a book's reputation — an accepted limitation,
   not a bug. The *correction* is walk-forward-honest; the *research vector* is not.
2. **Corrections leakage in `leaky`.** The `leaky` variant's correction is fit on the full
   library, so it saw books read after the target. It is labeled leaky everywhere and is a
   "today's config" reference, never a knowable-then number.
3. **`component_corrections` (DeltaTracker) enters no variant.** That table is retired to
   all-zero constants **and** is never read by the prediction path, so it changes nothing here.
   Recorded in `…_meta.json` for provenance.
4. **Interval coverage looks low — and that's an honest finding.** The per-fold interval
   recorded is the point-engine's `±1.645·resid_sd` (≈±0.21 WA), which is the residual of the
   near-perfect WA-from-category-averages regression, **not** a real prediction interval — so
   its coverage is far under nominal. The report separately scores the **calibrated served
   conformal interval** (`calibration/residuals.json`, density-bucketed by author analogs),
   which covers the honest walk-forward errors at ≈80% — the real calibration story.
5. **Ordering source = the Timeline sheet.** Read order comes from `BookRankingsNew.xlsx`
   Timeline (fiction rows only, renumbered after dropping interleaved nonfiction). Rated books
   **absent** from the Timeline (recent additions with no recorded order) are **placed last**
   by `(year_read, title)` — an owner decision, flagged via `in_timeline: false`. If you add
   those books to the Timeline, re-run to fold them into their true position.
6. **Future work — a fully-honest "variant 3."** Refitting the correction *per fold on the
   pool* is exactly what `honest` already does here (it's cheap in this engine). What remains
   deferred is per-fold recomputation of any *global* calibration layer (e.g. a revived
   DeltaTracker) and a per-fold **conformal** interval table — both out of scope for v1.
