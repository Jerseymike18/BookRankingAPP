# experiments/ — parked one-off scripts

One-off analyses, A/B tests, diagnostics, and one-time migrations/backfills that
are **not part of the live app**. Proven unreachable from the runtime roots
(`backend/main.py`, `scripts/export_static_data.py`, `test_engine.py`,
`validate_engine.py`, and the git hooks) by an import-closure check, then parked
here on **2026-07-06** to keep the top level to the live engine + its tests.

These are historical records: several import repo-root engine modules and would
need `sys.path` help to run from here. They are kept for provenance, not for
re-running as-is. The retired Streamlit prototype lives separately in `archive/`.

## A/B tests & modeling experiments (conclusions folded into the live engine or dropped)
- **ab_test_model_error.py** — raw (pre-correction) component-scoring error, Sonnet vs Opus; motivated `RESEARCH_MODEL = claude-opus-4-8`. (`ab_test_results.csv` is its output.)
- **rubric_test.py** — A/B: does a richer component-definition rubric improve LLM scoring?
- **correlation_experiment.py** — Track-2: can component correlation structure lower prediction error? (not adopted)
- **correlation_verify.py** — rigorous re-test of the correlation-smoothing gain. (not adopted)
- **shrinkage_estimator.py** — hierarchical-shrinkage A/B vs flat author/genre means; the winner became `predict_engine._shrink`.
- **personalize.py** — research-layer step 2: correct LLM "consensus" scores toward the owner's taste.
- **personalize_v2.py** — research-layer step 3: two personalization levers tested together.
- **taste_profile_pilot.py** — pilot: does a positive-signal taste profile help candidate generation?
- **book_engine.py** — early full Python port of the prediction engine + validation harness; superseded by `predict_engine.py`.

## Validation / diagnostics (read-only)
- **confirm_full.py** — full-corpus out-of-sample MAE confirmation of the blended engine (beyond the 32-book sample).
- **wa_rollup.py** — correctness check that the Python WA roll-up reproduces the workbook's WA exactly.
- **obscure_book_test.py** — behavioral validation: does the engine degrade gracefully on obscure books?
- **residual_bias_diagnostic.py** — does the live author_genre correction still reduce residual bias?
- **residual_diagnostics.py** — mechanism-level calibration report grouping `delta_log` residuals by dimension.

## Retro-sweep project (2026-07 recalibration)
- **retro_sweep.py** — repredict every read book under the current engine, leave-one-out (produced the Opus LOO `delta_log` rows).
- **retro_recalibrate.py** — recompute the DeltaTracker correction layer on the Opus retro residuals (**RETIRED** — out-of-sample gate failed).
- **retro_repredict_recs.py** — bulk-repredict recommendations under the current engine.
- **retro_recs_report.py** — read-only report of the reprediction shift + biggest movers.
- **retro_report.py** — read-only summary of the `retro_sweep_v1_shrunk` run (no writes, no API).

## One-time migrations / backfills (already applied to books.db)
- **migrate_to_db.py** — workbook → `books.db` (raw inputs only; the engine recomputes the rest).
- **migrate_nonfiction.py** — loaded nonfiction books from the workbook into `nonfiction_books`.
- **backfill_delta_log_from_workbook.py** — populated `delta_log` from the workbook's DeltaTracker history (idempotent).
- **backfill_series_numbers.py** — two-pass LLM backfill + verification of `series_number` across both tables.
- **backfill_year_read.py** — populated the `year_read` column in `books.db`.
- **update.py** — workbook-era post-add helper (pre-DB); obsolete now the DB engine recomputes on read.
