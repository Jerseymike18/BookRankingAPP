"""
engine_validation.py — the ENGINE's rigorous accuracy baseline, read from the
committed walk-forward artifacts. Feeds the Methodology page's "Does it actually
work?" / "Validation" sections.

READ-ONLY by construction. Reads three committed files under ``validation/``
(``walkforward_folds.jsonl``, ``walkforward_meta.json``, ``walkforward_rolling_mae.json``)
plus the served conformal residual table (``calibration/residuals.json``) via
the canonical ``intervals`` module. It NEVER:

  * runs the walk-forward harness (no API spend, no DB read),
  * reimplements prediction/interval math (the served-coverage number is
    computed through ``intervals.interval_for`` — the same code path the
    Predict page uses — so it can never drift from what a reader actually
    sees),
  * touches books.db.

Every number is a pure function of the committed artifacts, so the payload is
deterministic per commit — safe to snapshot byte-identically.

This module used to be part of ``track_record.py``. The two split when Track
Record went per-user (``track_record.py`` now builds a personal payload from
each tenant's ``delta_log``). Methodology still describes the ENGINE's
rigorous accuracy on the reference library, and reads it from here.

The reference-library variant surfaced is the **hybrid** (memory + web-grounded)
research vector, graded the honest no-leak way (past-only correction). The
**leaky** variant (correction fit on the full library) is excluded because it
saw future books. The retired ``resid_sd`` "old band" comparison is NOT
included — the only served interval is the conformal band.

Consumed by backend ``GET /api/engine-validation`` and snapshotted
(deterministically) to ``frontend/public/data/engine-validation.json`` by
``scripts/export_static_data.py``.
"""

import json
import os

import intervals

ROOT = os.path.dirname(os.path.abspath(__file__))
_VALID_DIR = os.path.join(ROOT, "validation")
_FOLDS = os.path.join(_VALID_DIR, "walkforward_folds.jsonl")
_META = os.path.join(_VALID_DIR, "walkforward_meta.json")
_RESIDUALS = os.path.join(ROOT, "calibration", "residuals.json")

# The reference-library variant surfaced: the live-served hybrid research
# vector, graded no-leak (past-only correction). Matches Track Record's old
# HEADLINE_VARIANT so the two agree on which variant is "the served one."
HEADLINE_VARIANT = "hybrid"


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _load():
    """Return (meta, folds) or None if any required artifact is missing/unreadable."""
    if not (os.path.exists(_FOLDS) and os.path.exists(_META)):
        return None
    try:
        with open(_META, encoding="utf-8") as fh:
            meta = json.load(fh)
        folds = []
        with open(_FOLDS, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    folds.append(json.loads(line))
    except (OSError, json.JSONDecodeError):
        return None
    return meta, folds


def _served_coverage(evaluated):
    """Coverage the CALIBRATED served interval achieves on the walk-forward folds.

    Buckets each fold by its honest same-author analog count and looks up that
    bucket's conformal half-width from calibration/residuals.json through the
    canonical ``intervals`` module (the live serving path), then checks whether
    the honest WA error falls inside. Returns None if no residual table loads.
    """
    table = intervals.load_residuals(_RESIDUALS)
    if not table:
        return None
    hits = tot = 0
    for f in evaluated:
        h = f["variants"][HEADLINE_VARIANT]
        n_author = h.get("n_author")
        if n_author is None:
            continue
        info = intervals.interval_for(table, n_author)
        if not info or info.get("half_width") is None:
            continue
        tot += 1
        if abs(h["wa_signed_error"]) <= info["half_width"] + 1e-12:
            hits += 1
    if not tot:
        return None
    return {"coverage": hits / tot, "n": tot}


def build_engine_validation():
    """Assemble the engine-validation payload, or None if artifacts aren't present.

    None mirrors the ``allow_404`` convention: the endpoint 404s and the
    snapshot stores JSON null, so the Methodology page shows a graceful
    "not yet available" state for the validation section only (the rest of
    the page still renders from /api/engine-parameters)."""
    loaded = _load()
    if loaded is None:
        return None
    meta, folds = loaded

    evaluated = [f for f in folds if "variants" in f]  # drop burn-in folds
    if not evaluated:
        return None

    honest_errs = [f["variants"][HEADLINE_VARIANT]["wa_abs_error"] for f in evaluated]
    raw_errs = [f["variants"]["raw"]["wa_abs_error"] for f in evaluated]
    mu = _mean([f["actual_wa"] for f in evaluated])
    naive = _mean([abs(f["actual_wa"] - mu) for f in evaluated])

    served = _served_coverage(evaluated)

    return {
        "available": True,
        "provenance": {
            "git_head": (meta.get("git_head") or "")[:12],
            "engine_hash": meta.get("engine_hash"),
            "backtest_generated_at": meta.get("generated_at"),
        },
        "headline": {
            "honest_wa_mae": round(_mean(honest_errs), 4),
            "raw_wa_mae": round(_mean(raw_errs), 4),
            "naive_wa_mae": round(naive, 4),
            "n_folds": len(evaluated),
            "n_books_total": meta.get("n_books_total"),
            "burn_in": meta.get("burn_in"),
        },
        "served_coverage": {
            "nominal": 0.80,
            "measured": round(served["coverage"], 4) if served else None,
            "n": served["n"] if served else None,
        },
    }


if __name__ == "__main__":  # quick manual smoke test
    import sys
    payload = build_engine_validation()
    if payload is None:
        print("engine-validation: artifacts not available", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
