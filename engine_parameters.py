"""
engine_parameters.py — assemble the LIVE engine parameters that the public
"How the Engine Works" (Methodology) page renders.

READ-ONLY by construction. It reads only:

  * the component schema + weights from ``books.db`` (via the engine tuple the
    caller passes in — ``predict_engine.build(source="db")``), i.e. the same
    ``gcomp_weights`` / ``genre_weights`` the engine itself uses;
  * the served shrinkage / model / smoothing constants straight off the modules
    that implement them (``reresearch_and_measure``, ``research_predict``,
    ``intervals``) — never hardcoded here, so a constant change in the engine is
    reflected on the page automatically;
  * the committed conformal residual table (``calibration/residuals.json``) for
    per-bucket interval half-widths, passed in by the caller.

It NEVER touches prediction math, writes anything, or spends tokens. Every value
is a pure function of committed state (DB + code constants + residual table), so
the payload is snapshot-deterministic — no timestamps, no git HEAD.

The DESIGN INTENT is anti-drift: the Methodology page interpolates these numbers
rather than typing them into prose, so a future engine commit that changes a
weight, a K constant, or the served model cannot silently make the page lie.
The page's *concepts* are hand-written prose; only the drift-prone *numbers* come
from here. The validation baselines (walk-forward MAE, measured interval
coverage) are deliberately NOT duplicated here — the page reuses
``track-record.json`` for those so the two public pages can never disagree.

Consumed by backend ``GET /api/engine-parameters`` and snapshotted
(deterministically) to ``frontend/public/data/engine-parameters.json`` by
``scripts/export_static_data.py``.
"""

import math
import sqlite3

import intervals
import research_predict as rp
import reresearch_and_measure as rm

DB = "books.db"

# Canonical category display order (matches db_loader.CATEGORY_OF_INTEREST).
CATEGORY_ORDER = ["Story", "Character", "Aesthetics", "Theme", "Worldbuilding"]

# The four weighted category averages that feed the WA-from-categories
# regression (Worldbuilding is folded into WA via its genre weight, not the
# regression). Mirrors predict_engine.REGRESSION_CATS — surfaced for the honest
# "this R² is a fit diagnostic, not a prediction interval" note on the page.
REGRESSION_INPUTS = ["Story", "Character", "Aesthetics", "Theme"]


def _num(v):
    """Coerce a stored weight to a JSON-safe float (NaN/inf/None -> None)."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _ordered_components(comps):
    """Order a category's components by the engine's canonical reference order
    (rm.LIVE), so the schema table reads Plot, Entertainment, Action, Ending …
    rather than SQLite insertion order. Unknown names sort last, by name."""
    def key(c):
        try:
            return (0, rm.LIVE.index(c))
        except ValueError:
            return (1, c)
    return sorted(comps, key=key)


def _correction_status(db_path=DB):
    """Read the retired-correction ledger (``component_corrections``) so the page
    can PROVE the layer is retired from committed data, not just assert it.

    ``applied_in_engine`` is a *code* fact: the serving path (predict_engine +
    research_predict) contains no reader for this table — there is no
    ``get_component_corrections`` anywhere — so the layer is both zeroed AND
    unwired. Degrades to ``present: False`` if the table is absent."""
    try:
        con = sqlite3.connect(db_path)
        rows = con.execute(
            "SELECT version, constant, blend_weight, active, decision "
            "FROM component_corrections"
        ).fetchall()
        con.close()
    except sqlite3.Error:
        return {"present": False, "applied_in_engine": False}
    if not rows:
        return {"present": False, "applied_in_engine": False}

    active = [r for r in rows if r[3]]
    considered = active or rows
    any_nonzero = any(
        abs(_num(r[1]) or 0.0) > 1e-12 or abs(_num(r[2]) or 0.0) > 1e-12
        for r in considered
    )
    versions = sorted({r[0] for r in considered if r[0]})
    decisions = sorted({r[4] for r in considered if r[4]})
    return {
        "present": True,
        "n_rows": len(rows),
        "n_active": len(active),
        "version": versions[0] if len(versions) == 1 else versions,
        "decision": decisions[0] if len(decisions) == 1 else decisions,
        "all_zero": not any_nonzero,
        "applied_in_engine": False,
        "max_blend_weight": max((_num(r[2]) or 0.0 for r in considered), default=0.0),
    }


def _interval_block(residuals):
    """Conformal-interval config + per-bucket half-widths.

    Config constants come from the ``intervals`` module (the single source of
    truth for bucketing, imported by both the LOO harness and the serving path).
    Per-bucket half-widths come from the committed residual table when present;
    if it is absent the buckets are still listed (definitions only) so the page
    can describe the mechanism without inventing widths."""
    buckets = []
    table_buckets = (residuals or {}).get("buckets") or {}
    for key in intervals.BUCKET_ORDER:
        b = {"key": key, "label": intervals.bucket_label(key)}
        info = table_buckets.get(key)
        if isinstance(info, dict):
            b["half_width"] = _num(info.get("half_width"))
            b["n_residuals"] = info.get("n")
            b["pooled"] = bool(info.get("pooled", False))
        buckets.append(b)

    block = {
        "nominal": intervals.COVERAGE_TARGET,
        "min_bucket_n": intervals.MIN_BUCKET_N,
        "analog_metric": "same-author analog count",
        "buckets": buckets,
        "residuals_available": residuals is not None,
    }
    if residuals is not None:
        # How the served residual table was calibrated (the analog engine's LOO
        # residuals). Coverage is NOT surfaced here — the page shows measured
        # coverage from track-record.json (honest walk-forward) so the two public
        # pages report one number.
        block["calibration"] = {
            "analog_mode": residuals.get("analog_mode"),
            "k_author": _num(residuals.get("k_author")),
            "k_genre": _num(residuals.get("k_genre")),
            "n_residuals": residuals.get("n_books"),
        }
    return block


def _cold_start_block(cold_term):
    """The word-count cold-start term (research_predict.apply_cold_start_term): a length
    adjustment applied ONLY on the cold slice — a book with no same-author analog (same-
    author count 0), where the correction is blind to length. Fitted per reader on their
    own leave-one-out (actual − corrected) residuals; the fitted slope/center come from the
    live engine's term (passed in), so this reflects the reader whose engine feeds the page.
    ``fitted`` is False when the term is off or the reader has too few books to fit it."""
    fitted = bool(cold_term and cold_term.get("slopes"))
    block = {
        "applied_when": "no same-author analog (same-author count = 0)",
        "feature": "log10(word count), centered",
        "fit": "OLS on the reader's leave-one-out (actual − corrected) residuals",
        "min_books_to_fit": rp.COLD_START_MIN_POOL,
        "fitted": fitted,
    }
    if fitted:
        block["slope_wa_per_dex"] = round(float(cold_term["slopes"][0]), 4)
        block["center_words"] = int(round(10 ** float(cold_term["mu"][0])))
        block["n_books_fit"] = cold_term.get("n")
    return block


def build_engine_parameters(books, gw, gcw, r2, resid_sd, residuals=None, db_path=DB,
                            cold_term=None):
    """Assemble the live engine-parameters payload from the prebuilt engine tuple.

    Args mirror the cached engine (``books, gw, gcw, …, r2, resid_sd``) so the
    backend can serve from its warm cache without a second DB build. ``residuals``
    is the loaded ``calibration/residuals.json`` (or None). Deterministic: no
    timestamps, no HEAD — safe to snapshot byte-identically."""
    cat_comps = books.attrs["category_components"]
    cat_order = [c for c in CATEGORY_ORDER if c in cat_comps] + [
        c for c in cat_comps if c not in CATEGORY_ORDER
    ]
    categories = [
        {"category": cat, "components": _ordered_components(cat_comps[cat])}
        for cat in cat_order
    ]
    n_components = sum(len(v) for v in cat_comps.values())
    genres = sorted(gw.keys())

    genre_category_weights = {
        g: {cat: _num(gw.get(g, {}).get(cat)) for cat in CATEGORY_ORDER}
        for g in genres
    }
    genre_component_weights = {
        g: {
            cat: {
                comp: _num(w)
                for comp, w in (gcw.get(g, {}).get(cat, {}) or {}).items()
            }
            for cat in cat_order
        }
        for g in genres
    }

    return {
        "schema": {
            "n_components": n_components,
            "n_categories": len(cat_comps),
            "n_genres": len(genres),
            "categories": categories,
            "component_order": [c for c in rm.LIVE],
        },
        "genre_category_weights": genre_category_weights,
        "genre_component_weights": genre_component_weights,
        # Served grounded-research shrinkage (reresearch_and_measure.correct_book,
        # method author_genre) + the correlation-smoothing pre-step. Read live.
        "shrinkage": {
            "corr_blend": rp.BLEND,
            "k_author": rm.K_AUTHOR,
            "k_genre": rm.K_GENRE,
            "slope_lift": rm.SLOPE_LIFT,
            "estimator": "n / (n + k)",
        },
        "interval": _interval_block(residuals),
        # WA-from-category-averages regression: near-deterministic, so its
        # residual is a FIT diagnostic — explicitly NOT the served interval.
        "regression": {
            "r2": round(_num(r2), 4) if _num(r2) is not None else None,
            "resid_sd": round(_num(resid_sd), 4) if _num(resid_sd) is not None else None,
            "inputs": REGRESSION_INPUTS,
        },
        "cold_start": _cold_start_block(cold_term),
        "correction": _correction_status(db_path),
        "models": {
            "research": rm.MODEL,
            "discover": rp.DISCOVER_MODEL,
        },
        "library": {
            "n_rated_books": int(len(books)),
        },
    }
