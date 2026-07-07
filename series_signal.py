"""
series_signal.py
================
A WITHIN-SERIES prediction signal: your own scores for earlier volumes of a
series help predict where the next volume lands. This is a small, pure helper
that the served prediction glue (research_predict.correct_and_predict) calls to
nudge the point estimate — it never touches the read-only engine
(predict_engine / db_loader / views) and never writes anything.

WHY THIS IS A SEPARATE, ADDITIVE SIGNAL (see validation/series_features_eval.md)
--------------------------------------------------------------------------------
The author+genre correction already leans on the mean of your same-author books,
and for most series in this library the same-author pool *is* the series (the
author wrote only that series). So a naive "average of prior volumes" largely
duplicates signal the engine already uses. This module is built to add the
*incremental* piece:

  * level      — the series runs higher/lower than the author generally (only
                 moves the estimate for multi-series authors, e.g. Sanderson,
                 where Stormlight ≠ the Mistborn/Warbreaker blend).
  * trajectory — the *direction* within the series (Malazan's late climb, a
                 Memory-Sorrow-Thorn-style decline), which neither the author
                 mean nor the series mean encodes.

LEAKAGE DISCIPLINE (the single most important correctness property)
-------------------------------------------------------------------
The signal is computed ONLY from the `pool` DataFrame it is handed. In the
walk-forward harness that pool is the past-only training frame (books read
strictly before the target), so "same-series rows in the pool" == prior-read
volumes, automatically. At serving time the pool is the full rated library.
Same code, different pool. The target title is always excluded, so re-predicting
an already-rated book never sees itself.

GRACEFUL DEGRADATION (a standalone / first volume predicts identically to today)
--------------------------------------------------------------------------------
  0 prior in-series volumes -> inert: returns the base WA unchanged.
  1 prior                   -> level only (no slope is defined from one point).
  >= 2 prior (>=2 distinct ordinals) -> level + trajectory available.

COMBINATION (a principled default, NOT tuned against the walk-forward set)
-------------------------------------------------------------------------
adjusted = (1 - w) * base + w * target,  where  w = n_prior / (n_prior + K_SERIES).
The shrinkage weight rises with the number of prior in-series reads, so a
two-book series barely moves the estimate while a ten-book series (Malazan/WoT)
is trusted more. K_SERIES is pre-registered below.
"""

import numpy as np

import views  # read-only reference; reuse its "no series" convention verbatim.

# The series pool is a subset of the (already-used) author pool and is more
# homogeneous, so a same-series book is at least as informative as a same-author
# one. But the base estimate ALREADY encodes author-level info (K_AUTHOR=0.5,
# aggressive), so this blend is deliberately CONSERVATIVE for small pools and only
# becomes dominant once a series is deep (where its level/trend are well
# estimated). K_SERIES=2 -> weight 0.33 @1 prior, 0.50 @2, 0.60 @3, 0.71 @5,
# 0.82 @9. Chosen a priori from this reasoning; NOT fit to the walk-forward MAE.
# A proper per-variant tune belongs in nested cross-validation (future work).
K_SERIES = 2.0

# Guardrails (safety, not tuned knobs): a 2-point slope can extrapolate wildly, so
# cap how far the series target may pull the estimate, and keep WA in range.
MAX_ADJUST = 1.5   # |adjusted - base| WA, hard cap
WA_MIN, WA_MAX = 0.0, 10.0

# The four evaluated modes. None / "off" is the production default (no-op).
MODES = ("level", "trajectory", "both")


def is_real_series(series):
    """True iff `series` names a real, numbered sequence (not the 'Standalone'
    sentinel or an empty/none marker). Mirrors views.series_aggregate's exclusion
    (`views._NON_SERIES`) plus lint_data's substring guard for ': Standalone'
    style groupings, so this module agrees with the rest of the app on what a
    series IS."""
    if series is None:
        return False
    s = str(series).strip().strip("'\"")
    low = s.lower()
    if not s or low in views._NON_SERIES or "standalone" in low:
        return False
    return True


def prior_volumes(title, series, pool, snum_map):
    """Prior-read volumes of `series` in `pool`, as a list of (ordinal, wa) sorted
    by ordinal (unknown ordinals last). Excludes the target `title`. `pool` is a
    books-shaped DataFrame (Book, Series, WA); `snum_map` maps title -> ordinal
    (books.db's series_number, which db_loader does not surface on the frame).

    Because `pool` is the caller's chosen frame (past-only in walk-forward, full
    library at serving), every returned row is by construction a book read before
    the target in the walk-forward case — no future leakage is possible here."""
    if not is_real_series(series) or pool is None or len(pool) == 0:
        return []
    target = str(title).strip()
    want = str(series).strip().strip("'\"")
    out = []
    for _, row in pool.iterrows():
        rs = row.get("Series")
        if not is_real_series(rs):
            continue
        if str(rs).strip().strip("'\"") != want:
            continue
        bt = str(row.get("Book", "")).strip()
        if bt == target:
            continue
        wa = row.get("WA")
        if wa is None or (isinstance(wa, float) and np.isnan(wa)):
            continue
        ordinal = snum_map.get(bt)
        try:
            ordinal = float(ordinal) if ordinal is not None else None
        except (TypeError, ValueError):
            ordinal = None
        out.append((ordinal, float(wa)))
    out.sort(key=lambda t: (t[0] is None, t[0] if t[0] is not None else 0.0))
    return out


def _slope(priors):
    """OLS slope + intercept of wa ~ ordinal over priors with a known ordinal.
    Returns (slope, intercept, xs, ys) or None when fewer than 2 DISTINCT ordinals
    exist (a slope is undefined from one x-value)."""
    xy = [(o, w) for (o, w) in priors if o is not None]
    xs = [o for o, _ in xy]
    if len(xy) < 2 or len(set(xs)) < 2:
        return None
    ys = [w for _, w in xy]
    b, a = np.polyfit(xs, ys, 1)   # np.polyfit returns [slope, intercept]
    return float(b), float(a), xs, ys


def series_target(base_wa, priors, target_ordinal, mode):
    """The WA the series signal points at, for one `mode`, or None when the mode
    is inert for this pool.

      level      -> flat mean of prior WAs (direction-agnostic).
      trajectory -> recency-anchored projection: last prior volume + slope to the
                    target ordinal ("last-volume + trend"); falls back to level
                    when no slope is defined (0/1 prior, or one distinct ordinal).
      both       -> the OLS regression line evaluated at the target ordinal
                    (mean-level AND slope together); falls back to level too.

    All targets are unclamped here; the caller clamps the *adjustment*."""
    if not priors:
        return None
    level = float(np.mean([w for _, w in priors]))
    if mode == "level":
        return level

    fit = _slope(priors)
    if fit is None or target_ordinal is None:
        # 1 prior, or no target ordinal -> only the level is defined.
        return level

    b, a, xs, ys = fit
    x = float(target_ordinal)
    if mode == "trajectory":
        # Anchor on the most-recently-numbered prior volume, project by the slope.
        last_x = max(xs)
        last_y = ys[xs.index(last_x)]
        return last_y + b * (x - last_x)
    if mode == "both":
        return a + b * x
    return level


def adjust_wa(base_wa, title, series, series_number, pool, snum_map,
              mode=None, k_series=K_SERIES):
    """Blend the series signal into a base WA point estimate. Returns
    (adjusted_wa, info). When `mode` is None/"off", or the book is a
    standalone/first volume (no prior in-series reads), returns base_wa unchanged
    and info["active"] is False — so production (mode=None) and non-series books
    are byte-identical to today.

    info carries what fired, for the eval report: mode, active, n_prior,
    n_distinct_ordinals, weight, target, base_wa, adjusted_wa, and the priors."""
    info = {"mode": mode, "active": False, "n_prior": 0, "n_distinct_ordinals": 0,
            "weight": 0.0, "target": None, "base_wa": float(base_wa),
            "adjusted_wa": float(base_wa), "priors": []}
    if mode in (None, "off"):
        return float(base_wa), info

    priors = prior_volumes(title, series, pool, snum_map)
    info["priors"] = priors
    info["n_prior"] = len(priors)
    info["n_distinct_ordinals"] = len({o for o, _ in priors if o is not None})
    if not priors:
        return float(base_wa), info      # inert: predicts exactly as today

    target = series_target(base_wa, priors, series_number, mode)
    if target is None:
        return float(base_wa), info

    n = len(priors)
    w = n / (n + float(k_series))
    adjusted = (1.0 - w) * float(base_wa) + w * float(target)

    # Clamp the pull (guards against a wild 2-point extrapolation) and the range.
    delta = adjusted - float(base_wa)
    if delta > MAX_ADJUST:
        adjusted = float(base_wa) + MAX_ADJUST
    elif delta < -MAX_ADJUST:
        adjusted = float(base_wa) - MAX_ADJUST
    adjusted = min(WA_MAX, max(WA_MIN, adjusted))

    info.update({"active": True, "weight": w, "target": float(target),
                 "adjusted_wa": adjusted})
    return adjusted, info


def build_snum_map(db_path="books.db"):
    """Read {title -> series_number} from books.db, read-only. Provided as a
    convenience for the served path and the eval harness; callers may also pass
    their own map (e.g. backend._series_number_map). series_number is not carried
    on the db_loader frame, so it must come from here."""
    import os
    import sqlite3
    out = {}
    try:
        uri = "file:" + os.path.abspath(db_path) + "?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        for t, n in con.execute("SELECT title, series_number FROM books"):
            out[str(t).strip()] = n
        con.close()
    except Exception:
        pass
    return out
