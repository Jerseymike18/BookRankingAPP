"""
track_record.py — assemble the public "track record" payload from the committed
walk-forward validation artifacts.

READ-ONLY by construction. It reads three committed files under ``validation/``
(``walkforward_folds.jsonl``, ``walkforward_meta.json``, ``walkforward_rolling_mae.json``)
plus the served conformal residual table (``calibration/residuals.json``) via the
canonical ``intervals`` module. It NEVER:

  * runs the walk-forward harness (no API spend, no DB read),
  * reimplements prediction / interval math (the served-coverage number is
    computed through ``intervals.interval_for`` — the same code path the Predict
    page uses — so it can never drift from what a reader actually sees),
  * touches books.db.

Every number is derived from the committed artifacts, so the payload is a pure
function of those files: deterministic, and it stays in lock-step with the
harness output instead of hardcoding figures that could silently go stale.

Consumed by backend ``GET /api/track-record`` and snapshotted (deterministically)
to ``frontend/public/data/track-record.json`` by ``scripts/export_static_data.py``.

The PUBLIC page shows the **honest** walk-forward variant (author+genre correction
fit on the past-only pool — the "what was knowable then" number). The **raw**
(no-correction) and **naive** (predict-the-mean) figures are included as honest
baselines. The **leaky** variant (correction fit on the full library) is
deliberately excluded — its correction saw future books.
"""

import json
import os

import intervals

ROOT = os.path.dirname(os.path.abspath(__file__))
_VALID_DIR = os.path.join(ROOT, "validation")
_FOLDS = os.path.join(_VALID_DIR, "walkforward_folds.jsonl")
_META = os.path.join(_VALID_DIR, "walkforward_meta.json")
_ROLLING = os.path.join(_VALID_DIR, "walkforward_rolling_mae.json")
_RESIDUALS = os.path.join(ROOT, "calibration", "residuals.json")

# The non-leaky variant surfaced publicly.
HEADLINE_VARIANT = "honest"

_CAVEATS = [
    "Chronological walk-forward: every book is predicted using only the books "
    "read before it (Timeline order), so no future information leaks into a "
    "prediction. This is the honest “what was knowable then” accuracy, "
    "not a leave-one-out fit that trains on future books.",
    "The 'honest' variant is shown. A 'leaky' variant that fits its correction "
    "on the full library (today's config) scores marginally better but saw "
    "future books, so it is excluded here.",
    "The grounded-research vectors embed post-publication reception (reviews, "
    "reputation) — an accepted hindsight caveat: the harness measures the "
    "engine's math, holding the researched inputs fixed.",
]


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _load():
    """Return (meta, folds, rolling) or None if any artifact is missing/unreadable."""
    if not (os.path.exists(_FOLDS) and os.path.exists(_META) and os.path.exists(_ROLLING)):
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
        with open(_ROLLING, encoding="utf-8") as fh:
            rolling = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return meta, folds, rolling


def _served_coverage(evaluated):
    """Coverage the CALIBRATED served interval achieves on the honest errors.

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
        h = f["variants"]["honest"]
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


def build_track_record():
    """Assemble the track-record payload, or None if the artifacts aren't present.

    None mirrors the ``allow_404`` convention: the endpoint 404s and the snapshot
    stores JSON null, so the page shows a graceful "not yet available" state.
    """
    loaded = _load()
    if loaded is None:
        return None
    meta, folds, rolling = loaded

    evaluated = [f for f in folds if "variants" in f]  # drop burn-in folds
    if not evaluated:
        return None

    # ── Headline: honest (star) + raw / naive baselines (leaky excluded) ──
    honest_errs = [f["variants"]["honest"]["wa_abs_error"] for f in evaluated]
    raw_errs = [f["variants"]["raw"]["wa_abs_error"] for f in evaluated]
    mu = _mean([f["actual_wa"] for f in evaluated])
    naive = _mean([abs(f["actual_wa"] - mu) for f in evaluated])
    headline = {
        "honest_wa_mae": round(_mean(honest_errs), 4),
        "raw_wa_mae": round(_mean(raw_errs), 4),
        "naive_wa_mae": round(naive, 4),
        "n_folds": len(evaluated),
        "n_books_total": meta.get("n_books_total"),
        "n_burn_in": meta.get("n_skipped"),
        "burn_in": meta.get("burn_in"),
    }

    # ── Fold-level (honest): predicted vs actual for the scatter ──
    fold_rows = []
    for f in evaluated:
        h = f["variants"]["honest"]
        fold_rows.append({
            "position": f["position"],
            "title": f["title"],
            "author": f["author"],
            "genre": f["genre"],
            "series": f.get("series"),
            "series_number": f.get("series_number"),
            "actual_wa": round(f["actual_wa"], 4),
            "predicted_wa": round(h["wa"], 4),
            "signed_error": round(h["wa_signed_error"], 4),
            "abs_error": round(h["wa_abs_error"], 4),
            "pool_size": f.get("pool_size"),
            "year_read": f.get("year_read"),
        })
    fold_rows.sort(key=lambda r: r["position"])

    # ── Rolling MAE (honest): slim passthrough of the committed series ──
    rolling_series = [
        {
            "position": s["position"],
            "title": s["title"],
            "pool_size": s["pool_size"],
            "window_n": s["window_n"],
            "honest_rolling_mae": round(s["honest_rolling_mae"], 4),
        }
        for s in rolling.get("series", [])
    ]
    rolling_out = {"window": rolling.get("window"), "series": rolling_series}

    # ── MAE by genre (honest), worst-first, with the raw baseline alongside ──
    by_genre_honest = {}
    by_genre_raw = {}
    for f in evaluated:
        g = f["genre"]
        by_genre_honest.setdefault(g, []).append(f["variants"]["honest"]["wa_abs_error"])
        by_genre_raw.setdefault(g, []).append(f["variants"]["raw"]["wa_abs_error"])
    genre_rows = [
        {
            "genre": g,
            "n": len(xs),
            "honest_mae": round(_mean(xs), 4),
            "raw_mae": round(_mean(by_genre_raw[g]), 4),
        }
        for g, xs in by_genre_honest.items()
    ]
    genre_rows.sort(key=lambda r: (-r["honest_mae"], r["genre"]))  # worst first, stable

    # ── Interval coverage: served conformal (kept) vs legacy resid_sd (removed) ──
    served = _served_coverage(evaluated)
    resid_sd_cov = _mean([1.0 if f["variants"]["honest"]["ci_inside"] else 0.0 for f in evaluated])
    interval_coverage = {
        "served_conformal": {
            "label": "density-bucketed conformal band (served on Predict / Read-queue)",
            "nominal": 0.80,
            "measured": round(served["coverage"], 4) if served else None,
            "n": served["n"] if served else None,
        },
        "legacy_resid_sd": {
            "label": "±1.645·resid_sd band (removed — was overconfident)",
            "nominal": 0.90,
            "measured": round(resid_sd_cov, 4),
            "n": len(evaluated),
        },
    }

    return {
        "available": True,
        # Provenance from the committed meta artifact — deterministic per commit.
        # NB: keys are deliberately NOT named "generated_at" (that name is scrubbed
        # by the snapshot determinism layer); these are committed constants, safe
        # to serve identically in local and static modes.
        "provenance": {
            "git_head": (meta.get("git_head") or "")[:12],
            "engine_hash": meta.get("engine_hash"),
            "backtest_generated_at": meta.get("generated_at"),
        },
        "headline": headline,
        "folds": fold_rows,
        "rolling": rolling_out,
        "mae_by_genre": genre_rows,
        "interval_coverage": interval_coverage,
        "caveats": _CAVEATS,
    }


if __name__ == "__main__":  # quick manual smoke test
    import sys
    payload = build_track_record()
    if payload is None:
        print("track-record: artifacts not available", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(payload, indent=2, ensure_ascii=False)[:2000])
