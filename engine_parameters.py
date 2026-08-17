"""
engine_parameters.py — assemble the LIVE engine parameters that the
"How the Engine Works" (Methodology) page renders.

READ-ONLY by construction. It reads only:

  * the component schema + weights from the engine tuple the caller passes in —
    the CALLER'S tenant-scoped engine build, so a hosted user sees their own
    effective weights (global defaults overlaid with any overrides) and their
    own library size, not the reference library's;
  * the served shrinkage / model / smoothing constants straight off the modules
    that implement them (``reresearch_and_measure``, ``research_predict``,
    ``intervals``) — never hardcoded here, so a constant change in the engine is
    reflected on the page automatically;
  * the committed conformal residual table (``calibration/residuals.json``) for
    per-bucket interval half-widths, passed in by the caller (global — the
    residual table is calibrated once, on the reference library);
  * the caller's cold-start term (fitted per tenant, or their onboarding
    word-count preference) and whether their prediction model is their own fit
    or the borrowed seed calibration.

It NEVER touches prediction math, writes anything, or spends tokens. Every value
is a pure function of the passed-in engine state, so for the default (seed) user
the payload is snapshot-deterministic — no timestamps, no git HEAD.

The DESIGN INTENT is anti-drift: the Methodology page interpolates these numbers
rather than typing them into prose, so a future engine commit that changes a
weight, a K constant, or the served model cannot silently make the page lie.
The page's *concepts* are hand-written prose; only the drift-prone *numbers* come
from here. The validation baselines (walk-forward MAE, measured interval
coverage) are deliberately NOT duplicated here — the Methodology page reads
them separately from ``/api/engine-validation`` (reference-library walk-forward),
which is decoupled from ``/api/track-record`` (now personal, per-user) by
design so a change to one payload can't silently redefine the other.

Consumed by backend ``GET /api/engine-parameters`` (tenant-scoped via the auth
dependency) and snapshotted (deterministically, as the default user) to
``frontend/public/data/engine-parameters.json`` by
``scripts/export_static_data.py``.
"""

import math

import intervals
import research_predict as rp
import reresearch_and_measure as rm
import score_anchors as sa
import views

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


def _genre_prior_source(cold_term):
    """Which evidence fills the genre_prior slot: "stars" | "favorites" | "off".

    Distinguished structurally rather than by a flag, so it can't drift from what the
    backend actually built: the favorites prior emits uniform 1.0 weights with the
    magnitude in ``base``, while the star prior folds signed per-genre WA offsets into the
    map (base 1.0) because the cap has to be applied per genre."""
    gp = (cold_term or {}).get("genre_prior")
    if not gp:
        return "off"
    weights = list((gp.get("map") or {}).values())
    if weights and all(float(w) == 1.0 for w in weights):
        return "favorites"
    return "stars"


def _cold_start_block(cold_term):
    """The word-count cold-start term (research_predict.apply_cold_start_term): a length
    adjustment applied ONLY on the cold slice — a book with no same-author analog (same-
    author count 0), where the correction is blind to length. ``source`` says where THIS
    reader's slope comes from:

      * ``fitted`` — OLS on their own leave-one-out residuals (``n`` > 0, i.e. they have
        at least COLD_START_MIN_POOL word-counted rated books);
      * ``preference`` — the onboarding word-count preference of a reader too new to fit
        (a stated slope pivoting around a typical-novel length, ``n`` == 0);
      * ``off`` — no fitted term and no stated preference.

    ``author_prior`` flags the independent favorite-authors bump (new readers' stated
    favorites + analogs), which rides along whatever the word-count source is — applied on
    the same no-same-author-analog slice. ``genre_prior`` flags the parallel per-genre
    adjustment, applied instead on the no-same-genre-analog slice (n_genre == 0), and
    ``genre_prior_source`` says which evidence fills it: ``stars`` for offsets derived from
    the reader's imported Goodreads ratings (graded and SIGNED — a below-average genre
    lowers the prediction), ``favorites`` for their stated favorite genres (a positive bump
    only), or ``off``."""
    slopes = (cold_term or {}).get("slopes")
    n_fit = int((cold_term or {}).get("n") or 0)
    source = "fitted" if (slopes and n_fit > 0) else ("preference" if slopes else "off")
    block = {
        "applied_when": "no same-author analog (same-author count = 0)",
        "feature": "log10(word count), centered",
        "fit": "OLS on the reader's leave-one-out (actual − corrected) residuals",
        "min_books_to_fit": rp.COLD_START_MIN_POOL,
        "source": source,
        "fitted": source == "fitted",
        "author_prior": bool((cold_term or {}).get("author_prior")),
        "genre_prior": bool((cold_term or {}).get("genre_prior")),
        "genre_prior_source": _genre_prior_source(cold_term),
    }
    if slopes:
        block["slope_wa_per_dex"] = round(float(slopes[0]), 4)
        block["center_words"] = int(round(10 ** float(cold_term["mu"][0])))
        if source == "fitted":
            block["n_books_fit"] = n_fit
    return block


def _score_anchor_block(anchors):
    """The reader's prose→number research scale (score_anchors). `anchors` is a
    {band: value} table for THIS tenant (None → the canonical defaults). Band
    labels/defaults are read live off score_anchors.BANDS so the page can't drift
    from the scale the engine actually applies; `customized` says whether this
    reader's remap is the identity."""
    values = anchors or sa.DEFAULTS
    return {
        "bands": [
            {"key": b["key"], "label": b["label"], "canonical": b["hint"],
             "default": b["default"],
             "value": round(float(values.get(b["key"], b["default"])), 4)}
            for b in sa.BANDS
        ],
        "customized": not sa.is_default(values),
        "applied_to": "raw research components, before correction",
    }


def _series_model_block():
    """The series-quality model's constants, read LIVE off views so the Series
    Breakdown page can never quote a coefficient the engine no longer uses.

    Each term is a deviation from the series' own average, never a level — see the
    model notes in views.py. Commitment is reported separately from the other three
    because it sits OUTSIDE their shared clamp."""
    return {
        "formula": "avg_WA + clamp(Consistency + Peak + Finale + Unfinished, "
                   "±quality_clamp) + Evidence",
        "quality_clamp": views._QUALITY_CLAMP,
        "consistency": {
            "k": views._CONSISTENCY_K,
            "cap": views._CONSISTENCY_CAP,
            "shrink_k": views._CONSISTENCY_SHRINK_K,
            "measures": "library percentile of the series' WEAKEST volume, "
                        "centred on the median, shrunk by n/(n+shrink_k)",
        },
        "peak": {"k": views._PEAK_K, "cap": views._PEAK_CAP,
                 "measures": "max_WA - avg_WA"},
        "finale": {"k": views._FINALE_K, "cap_up": views._FINALE_CAP_UP,
                   "cap_down": views._FINALE_CAP_DOWN,
                   "measures": "final volume's Ending - mean Ending",
                   "requires_complete": True},
        "unfinished": {
            "penalty": views._UNFINISHED_PENALTY,
            "measures": "standing charge on a series that has not ended — distinct "
                        "from a suppressed Finale, which only means 'no evidence "
                        "about the ending'",
            "applies_when": "completeness is KNOWN and the series is ongoing; a "
                            "caller with no completeness data charges nothing",
        },
        "evidence": {
            "min_books": views._MIN_EVIDENCE_N,
            "penalty": views._INSUFFICIENT_EVIDENCE_PENALTY,
            "in_quality_budget": False,
            "measures": "held back below min_books — no within-series information",
        },
        # There is deliberately NO length term: this reader finishes every series
        # they start, so book count measures how much they read, not how good it
        # was. The retired bonus correlated +0.88 with count; Consistency ≈ 0.00.
        "length_bonus": None,
    }


def build_engine_parameters(books, gw, gcw, r2, resid_sd, residuals=None,
                            cold_term=None, model_source="own", min_own_fit=None,
                            blend_weight=None, anchors=None):
    """Assemble the live engine-parameters payload from the prebuilt engine tuple.

    Args mirror the cached engine (``books, gw, gcw, …, r2, resid_sd``) so the
    backend can serve from its warm cache without a second DB build — pass the
    CALLER'S tenant engine so every number is theirs. ``residuals`` is the loaded
    ``calibration/residuals.json`` (or None). ``model_source`` is "own" when the
    regression/correction calibration is fit on the reader's own library,
    "borrowed_seed" for a tenant riding the reference library's calibration whole,
    and "blended" in between — cold start shrinks smoothly rather than switching,
    so ``blend_weight`` (0..1, the fraction carried by the reader's OWN fit) is the
    honest figure and ``min_own_fit`` is retained as a reference threshold only.
    ``anchors`` is the caller's rating-scale table (score_anchors.load_anchors);
    None reports the canonical defaults.
    Deterministic: no timestamps, no HEAD — safe to snapshot byte-identically for
    the default user."""
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
        "score_anchors": _score_anchor_block(anchors),
        "series_model": _series_model_block(),
        "models": {
            "research": rm.MODEL,
            "discover": rp.DISCOVER_MODEL,
        },
        "library": {
            "n_rated_books": int(len(books)),
            # Whose calibration the reader's predictions run on right now, and in
            # what proportion (blend_weight = the share carried by their OWN fit).
            "model_source": model_source,
            "min_own_fit": min_own_fit,
            "blend_weight": blend_weight,
        },
    }
