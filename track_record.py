"""
track_record.py — assemble a **per-user** Track Record payload from the
tenant's own prediction log.

READ-ONLY, PURE, zero-API. Given already-deduped genuine finished
``delta_log`` rows (Requirement-1 finished-only + Requirement-2 one
authoritative row, as produced by ``delta_log_view.visible_rows``) plus the
committed conformal residual table, it returns:

  * headline MAE (personal predicted-vs-actual across your books),
  * raw MAE (uncorrected research vector, reconstructed as ``pred_wa -
    corr_wa``; None when the correction split is missing for too many rows),
  * naive baseline (predict the reader's own mean WA),
  * one row per book (title, genre, actual, predicted, error, read year),
  * a rolling-MAE curve over reading order (personal "getting smarter"),
  * MAE by genre (worst-first),
  * served conformal band coverage on your books (via
    ``intervals.interval_for(residuals, n_author)`` — the SAME code path the
    Predict page uses, so the two can never drift).

It NEVER runs the engine, calls the LLM, reads books.db, or re-derives
prediction math. The frozen ``pred_wa`` / ``corr_wa`` / ``n_author`` stored on
each ``delta_log`` row (captured at forecast time by
``research_predict.build_prediction_meta``) are the sole numerical inputs.

Consumed by backend ``GET /api/track-record`` (tenant-scoped via the auth
dependency), which fetches, dedups, and calls this builder. The static export
runs it as the default user, so ``frontend/public/data/track-record.json`` is
the seed user's personal Track Record — identical across snapshot rebuilds.

The retired ``resid_sd`` "old band" comparison is deliberately absent. The
only served interval is the conformal band; showing coverage for a band the
engine no longer serves would be misleading. Engine-wide walk-forward
validation (reference library) moved to ``engine_validation.py`` and is
consumed by the Methodology page — the two payloads are decoupled by design.
"""

from __future__ import annotations

import intervals

# Minimum number of visible-deduped delta_log rows before the endpoint returns
# a payload. Below this a caller (backend/main.py) returns 404 → the frontend
# shows a "not enough yet" empty state. Owner-picked threshold — enough for a
# meaningful MAE and a stub rolling curve without a long wait.
MIN_TRACK_RECORD = 8

# Rolling-MAE window (trailing K books). Small enough to move visibly as a
# reader's history grows past MIN_TRACK_RECORD; matches the pace of the
# reference-library rolling series.
ROLLING_WINDOW = 12


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _round(v, ndigits=4):
    return round(v, ndigits) if v is not None else None


def _served_coverage(rows, residuals):
    """Coverage the CALIBRATED served interval achieves on this user's rows.

    Buckets each row by its stored same-author analog count and looks up the
    bucket's conformal half-width from calibration/residuals.json through the
    canonical ``intervals`` module, then checks whether the row's actual WA
    lies inside ``pred_wa ± half_width``. Returns None if no residual table
    loads or no row carries an ``n_author`` (older rows may not).
    """
    if not residuals:
        return None
    hits = tot = 0
    for r in rows:
        n_author = r.get("n_author")
        if n_author is None:
            continue
        info = intervals.interval_for(residuals, n_author)
        if not info or info.get("half_width") is None:
            continue
        try:
            err = abs(float(r["pred_wa"]) - float(r["act_wa"]))
        except (TypeError, ValueError, KeyError):
            continue
        tot += 1
        if err <= info["half_width"] + 1e-12:
            hits += 1
    if not tot:
        return None
    return {"coverage": hits / tot, "n": tot}


def _rolling_series(sorted_rows, window):
    """Trailing-window MAE over reading order. ``sorted_rows`` is oldest-read
    first; each emitted point carries the trailing ``window`` (or fewer, during
    ramp-up) rows' MAE. Series length matches len(sorted_rows)."""
    series = []
    for i, r in enumerate(sorted_rows, start=1):
        start = max(0, i - window)
        chunk = sorted_rows[start:i]
        errs = []
        for x in chunk:
            try:
                errs.append(abs(float(x["pred_wa"]) - float(x["act_wa"])))
            except (TypeError, ValueError, KeyError):
                pass
        if not errs:
            continue
        series.append({
            "position": i,
            "title": r.get("title") or "",
            "pool_size": i,
            "window_n": len(errs),
            "honest_rolling_mae": round(sum(errs) / len(errs), 4),
        })
    return series


def _personal_caveats():
    return [
        "Each row is the score the engine served for a book before you read "
        "it, compared to the rating you ended up giving. The prediction is "
        "frozen at forecast time — retraining the engine later doesn't move "
        "these numbers.",
        "For books rated before per-book prediction logging existed, the "
        "prediction is a leave-one-out reconstruction (the engine trained on "
        "every other book of yours and then predicted this one). Genuine and "
        "reconstructed rows are graded the same way.",
        "The grounded-research vectors embed post-publication reception "
        "(reviews, reputation) — an accepted hindsight caveat: the harness "
        "measures the engine's math, holding the researched inputs fixed.",
    ]


def build_track_record(rows, read_order, residuals=None,
                       book_meta=None, min_books=MIN_TRACK_RECORD):
    """Build the per-user Track Record payload, or None if too little data.

    Args:
      rows: deduped genuine finished delta_log rows (dicts with pred_wa,
        act_wa, and — optionally — corr_wa, pred_genre, pred_author,
        n_author). Order is not important; this function re-sorts by reading
        recency via ``read_order``.
      read_order: {normalized-title: recency-rank} where higher rank = more
        recently read. Same encoding /api/delta-log uses.
      residuals: the loaded calibration/residuals.json (or None). Used to
        compute served-band coverage on this user's rows.
      book_meta: optional {normalized-title: {"genre", "author", "series",
        "series_number", "year_read", "current_wa"}} from the tenant's
        books table + engine. ``current_wa`` is the book's WA recomputed
        live from its current component ratings + weights — it PREFERS this
        over the delta_log's frozen ``act_wa`` when present, so the "actual"
        surfaced on the scatter/rolling curve reflects the reader's current
        rating (they may have edited it after the book was first marked
        finished). Other keys are used as fallbacks when a delta_log row
        is missing pred_genre etc.
      min_books: threshold below which the endpoint reports "not enough
        yet" (returns None). Defaults to MIN_TRACK_RECORD.

    Returns the payload dict, or None if fewer than ``min_books`` rows carry
    both a numeric pred_wa and act_wa.
    """
    read_order = read_order or {}
    book_meta = book_meta or {}

    def _norm(t):
        return (t or "").strip().lower()

    # Keep only rows with a numeric pred/actual pair — those are the ones we
    # can grade. Everything else falls out silently (no exceptions). The
    # "actual" preferred is the reader's CURRENT WA (recomputed live from
    # their current components + weights) — the delta_log's frozen act_wa
    # is only used as a fallback for books whose current-WA lookup fails
    # (e.g. a renamed/removed row still present in the log).
    cleaned = []
    for r in rows or []:
        try:
            pred = float(r["pred_wa"])
        except (TypeError, ValueError, KeyError):
            continue
        key = _norm(r.get("title"))
        meta = book_meta.get(key, {})
        cur = meta.get("current_wa")
        try:
            act = float(cur) if cur is not None else float(r["act_wa"])
        except (TypeError, ValueError, KeyError):
            continue
        cleaned.append({
            **r,
            "_key": key,
            "_pred": pred,
            "_act": act,
            "_abs": abs(pred - act),
            "_signed": pred - act,
            "_genre": r.get("pred_genre") or meta.get("genre") or "Unknown",
            "_author": r.get("pred_author") or meta.get("author") or "",
            "_series": meta.get("series"),
            "_series_number": meta.get("series_number"),
            "_year_read": meta.get("year_read"),
            "_rank": read_order.get(key, 0),
        })

    if len(cleaned) < max(1, int(min_books)):
        return None

    # Oldest-read → newest for rolling / folds display (higher rank = more
    # recent, so ascending rank = oldest first). Rows without a rank sort at
    # the very start (they land before everything ranked, which is fine — an
    # unranked historical import behaves like a pre-history seed).
    ordered = sorted(cleaned, key=lambda r: r["_rank"])

    # ── Headline ──
    honest_errs = [r["_abs"] for r in ordered]
    # Raw baseline: reconstruct the uncorrected research WA per row when the
    # correction split is stored. `raw_pred = pred_wa - corr_wa`. Rows missing
    # corr_wa (e.g. hand-scored books) fall out of the raw baseline but stay
    # in every other stat. We only surface raw_wa_mae when enough rows carry
    # a correction — otherwise a handful of noisy rows would dominate.
    raw_rows = [r for r in ordered if r.get("corr_wa") is not None]
    raw_wa_mae = None
    if len(raw_rows) >= min(min_books, 5):
        raw_errs = [abs((r["_pred"] - float(r["corr_wa"])) - r["_act"])
                    for r in raw_rows]
        raw_wa_mae = _round(_mean(raw_errs))

    mu = _mean([r["_act"] for r in ordered])
    naive_wa_mae = _round(_mean([abs(r["_act"] - mu) for r in ordered]))

    headline = {
        "wa_mae": _round(_mean(honest_errs)),
        "raw_wa_mae": raw_wa_mae,
        "naive_wa_mae": naive_wa_mae,
        "n_books": len(ordered),
    }

    # ── Folds (one row per book, oldest → newest) ──
    fold_rows = [
        {
            "position": i,
            "title": r.get("title") or "",
            "author": r["_author"],
            "genre": r["_genre"],
            "series": r["_series"],
            "series_number": r["_series_number"],
            "actual_wa": round(r["_act"], 4),
            "predicted_wa": round(r["_pred"], 4),
            "signed_error": round(r["_signed"], 4),
            "abs_error": round(r["_abs"], 4),
            "pool_size": i,
            "year_read": r["_year_read"],
        }
        for i, r in enumerate(ordered, start=1)
    ]

    # ── Rolling MAE (personal "getting smarter" curve) ──
    rolling_series = _rolling_series(ordered, ROLLING_WINDOW)
    rolling_out = {"window": ROLLING_WINDOW, "series": rolling_series}

    # ── MAE by genre (personal, worst-first) ──
    by_g_honest = {}
    by_g_raw = {}
    for r in ordered:
        g = r["_genre"]
        by_g_honest.setdefault(g, []).append(r["_abs"])
        if r.get("corr_wa") is not None:
            raw_err = abs((r["_pred"] - float(r["corr_wa"])) - r["_act"])
            by_g_raw.setdefault(g, []).append(raw_err)
    genre_rows = []
    for g, errs in by_g_honest.items():
        genre_rows.append({
            "genre": g,
            "n": len(errs),
            "honest_mae": _round(_mean(errs)),
            # None when this genre has no raw rows — client renders "—".
            "raw_mae": _round(_mean(by_g_raw[g])) if by_g_raw.get(g) else None,
        })
    genre_rows.sort(key=lambda r: (-(r["honest_mae"] or 0), r["genre"]))

    # ── Served conformal coverage (personal) ──
    served = _served_coverage(ordered, residuals)
    interval_coverage = {
        "served_conformal": {
            "label": "density-bucketed conformal band (served on Predict / Read-queue)",
            "nominal": 0.80,
            "measured": _round(served["coverage"]) if served else None,
            "n": served["n"] if served else None,
        },
    }

    return {
        "available": True,
        "provenance": {
            # No timestamps, no git HEAD — every field is derived from stored
            # per-user data, so the default-user snapshot stays byte-identical
            # across export rebuilds when the DB hasn't moved.
            "data_source": "personal",
            "min_books": MIN_TRACK_RECORD,
        },
        "headline": headline,
        "folds": fold_rows,
        "rolling": rolling_out,
        "mae_by_genre": genre_rows,
        "interval_coverage": interval_coverage,
        "caveats": _personal_caveats(),
    }


def enrich_missing_meta(visible, all_rows):
    """Fill missing mechanism-metadata on each visible row from other rows for
    the same title. Only writes *absent* fields — the frozen ``pred_wa`` /
    ``act_wa`` on the authoritative row are never touched, so the delta_log
    invariant ("the prediction shown is the one recorded at forecast time")
    still holds.

    Reason this exists: pre-mechanism-metadata live rows won lookup priority in
    ``delta_log_view.visible_rows`` (correctly — the live pred is the true
    read-time forecast), but they lack the ``n_author`` / ``corr_wa`` /
    ``pred_genre`` fields the served-coverage and raw-baseline stats need.
    The later retro_sweep LOO row for the same book carries that metadata; we
    borrow only the missing fields. The corr_wa borrowed this way is a
    proxy (fit on a slightly different training pool), which is why the
    raw-MAE stat is deliberately best-effort and not a headline number.
    """
    fields = ("corr_wa", "n_author", "n_genre", "pred_genre",
              "pred_author", "pred_words")
    by_title = {}
    for r in all_rows or []:
        key = (r.get("title") or "").strip().lower()
        by_title.setdefault(key, []).append(r)
    for v in visible:
        key = (v.get("title") or "").strip().lower()
        for other in by_title.get(key, ()):
            if other is v:
                continue
            for f in fields:
                if v.get(f) in (None, "") and other.get(f) not in (None, ""):
                    v[f] = other[f]
    return visible


if __name__ == "__main__":  # quick manual smoke test against the seed user
    import json
    import os
    import sys

    import db_backend
    import db_write
    import delta_log_view

    ROOT = os.path.dirname(os.path.abspath(__file__))
    _RESIDUALS_PATH = os.path.join(ROOT, "calibration", "residuals.json")
    residuals = intervals.load_residuals(_RESIDUALS_PATH)

    con = db_backend.connect(db_write.DB)
    # Match the /api/delta-log query, plus the columns the builder wants.
    rows = con.execute(
        "SELECT id, title, pred_wa, act_wa, pred_genre, pred_author, "
        "corr_wa, n_author, n_genre, pred_words, tag, logged_at, user_id "
        "FROM delta_log WHERE user_id=? ORDER BY id DESC",
        (db_backend.DEFAULT_USER_ID,),
    ).fetchall()
    cols = ["id", "title", "pred_wa", "act_wa", "pred_genre", "pred_author",
            "corr_wa", "n_author", "n_genre", "pred_words", "tag",
            "logged_at", "user_id"]
    entries = [dict(zip(cols, r)) for r in rows]

    finished, read_order, book_meta = set(), {}, {}
    for (t, g, a, s, sn, yr, mo, seq) in con.execute(
        "SELECT title, genre, author, series, series_number, year_read, "
        "read_month, read_seq FROM books WHERE user_id=? AND status=?",
        (db_backend.DEFAULT_USER_ID, "finished"),
    ).fetchall():
        key = (t or "").strip().lower()
        finished.add(key)
        book_meta[key] = {
            "genre": g, "author": a, "series": s, "series_number": sn,
            "year_read": yr,
        }
        if yr is not None:
            read_order[key] = (int(yr) * 100 + (int(mo) if mo else 0)) * 1_000_000 \
                + (int(seq) if seq else 0)
    con.close()

    # Current WA per finished book (from the engine, recomputed live) — the
    # actual score the builder should show on the scatter.
    import db_loader as _dl
    books_df, _gw, _gcw = _dl.load_from_db("books.db", user_id=db_backend.DEFAULT_USER_ID)
    for _, row in books_df.iterrows():
        k = str(row.get("Book") or "").strip().lower()
        try:
            wa = float(row["WA"])
        except (TypeError, ValueError, KeyError):
            continue
        book_meta.setdefault(k, {})["current_wa"] = wa

    visible = delta_log_view.visible_rows(
        entries, finished, db_write.DELTA_BACKFILL_MARKER, read_order=read_order)
    visible = enrich_missing_meta(visible, entries)
    payload = build_track_record(visible, read_order, residuals=residuals,
                                 book_meta=book_meta)
    if payload is None:
        print("track-record: not enough personal data yet", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(payload["headline"], indent=2))
    print("interval:", json.dumps(payload["interval_coverage"], indent=2))
    print(f"folds={len(payload['folds'])} rolling={len(payload['rolling']['series'])} "
          f"genres={len(payload['mae_by_genre'])}")
