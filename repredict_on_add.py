"""
repredict_on_add.py
===================
Automatic, scoped re-prediction of unread books when a just-finished book
establishes or shifts the baseline their prediction leans on.

MOTIVATING CASE
  You read *A Game of Thrones* and add it. GRRM now has his first real data
  point, so the author-correction layer for his other four ASOIAF books (still
  in Recommendations) moves from n=0 to n=1. This module re-predicts exactly
  those affected books — in place — using the SAME engine path as a fresh
  prediction, and logs the old→new move to delta_log.

WHAT THIS IS (and is NOT)
  - It is an auto-triggered, SCOPED version of retro_repredict_recs.py. Same
    prediction path (research_book -> hybrid grounded overrides -> the validated
    author_genre correction), same in-place write (db_write.update_recommendation
    _scores), same clamp. No engine math is reimplemented here.
  - It is NOT a new source of truth and adds NO schema. The affected set is
    derived entirely from columns already on every recommendation row (author,
    genre) — this engine has no curated per-book analog list; "analogs" ARE the
    same-author and same-genre statistical pools (reresearch_and_measure.correct
    _book), so author ∪ gated-genre fully covers the brief's affected set.

AFFECTED SET (priority order)
  1. Same AUTHOR as the finished book — always.
  2. Same GENRE — only if the genre-tier baseline moved by more than a noise
     floor (GENRE_REPREDICT_GATE_CAP). With the researched path's K_GENRE=6 one
     new point among dozens barely moves the genre layer, so this gate normally
     suppresses genre-peers and only fires for thin genres.

THE CACHE DEPENDENCY (important)
  The researched author-correction only sees a book if it is in the research
  cache: reresearch_and_measure.build_pairs SKIPS any rated book with no cached
  LLM vector. So for the finished book to actually move its author's cohort, it
  must have a cached LLM vector. If it was read without ever being predicted,
  this module (when research_trigger=True) runs one grounded research call to
  put its vector in the cache BEFORE re-predicting the cohort — otherwise the
  author layer would still see n=0 and the cohort would not move.

ORDERING (must hold)
  The caller fires this AFTER the finished book is committed to the DB and the
  engine is invalidated, so build_pairs / the library already reflect n=1.

SAFETY
  - Reads recommendations by direct SELECT (reads are unrestricted); every WRITE
    goes through db_write (update_recommendation_scores + log_delta) — no direct
    write SQL, no new write functions.
  - dry_run=True computes and reports without writing or logging (used to preview
    the gate + movers before churning anything).
  - Fully injectable (get_engine / cache / web) so it is testable with no network
    and against a throwaway DB copy.

RUN (manual dry-run preview)
  python3 repredict_on_add.py "A Game of Thrones" --dry-run
"""

import argparse
import os
import sqlite3
import db_backend
import threading
from collections import OrderedDict

import numpy as np
import pandas as pd

import db_write
import research_predict as rp
import reresearch_and_measure as rm
import hybrid_researcher as hybrid

LIVE = rp.LIVE  # canonical 14 components, reference order

# ---------------------------------------------------------------------------
# Genre gate. Re-predict same-genre peers only when the genre-tier baseline
# actually moved. The threshold is min(cap, ½ · median conformal half-width):
# the cap is the operative floor (the conformal half-widths are ~1–2 WA points,
# so ½ of them is ~0.5–1.0, far above the cap — the cap wins in practice, which
# is the intended behaviour: a sub-0.05-WA genre nudge is noise, not a reason to
# churn dozens of rows). Named constant so the dial is one line.
# ---------------------------------------------------------------------------
GENRE_REPREDICT_GATE_CAP = 0.05
RESIDUALS_PATH = os.path.join("calibration", "residuals.json")

# Safety cap on how many SAME-GENRE peers one add may re-predict. Author-peers are
# never capped (they are the primary signal). When the gate fires on a thin genre
# the peer set can be large; we re-predict the ones a genre-baseline shift moves
# MOST (peers whose own author has the least rated support, so their author layer
# is weak and the genre layer dominates) and report the overflow as
# `capped_genre_peers` — bounded churn, never a silent truncation.
MAX_GENRE_PEERS_PER_ADD = 25


# ---------------------------------------------------------------------------
# Recent-report store. on-add re-prediction runs in the background (the add-book
# request returns immediately), so the finished report is stashed here under a
# token the caller polls for. Thread-safe; bounded ring buffer.
# ---------------------------------------------------------------------------
_REPORTS = OrderedDict()
_REPORTS_LOCK = threading.Lock()
_REPORTS_MAX = 64


def record_report(token, report):
    """Stash a finished report under `token` for later retrieval (evicting the
    oldest beyond _REPORTS_MAX)."""
    with _REPORTS_LOCK:
        _REPORTS[token] = report
        while len(_REPORTS) > _REPORTS_MAX:
            _REPORTS.popitem(last=False)


def get_report(token):
    """Return the stashed report for `token`, or None if not ready yet."""
    with _REPORTS_LOCK:
        return _REPORTS.get(token)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _clamp(v):
    """Clamp a corrected score to the defined 0–10 range (the author_genre
    correction can extrapolate a hair past 10 for a near-max book; the validated
    writer rejects out-of-range, so clamp preserves the score). Mirrors
    retro_repredict_recs."""
    return min(10.0, max(0.0, float(v)))


def _fully_cached(cache, title):
    """True iff `title` has a complete cached LLM vector (all 14 components) —
    the exact condition build_pairs requires to admit a book to the correction
    pool."""
    e = cache.get(title) if isinstance(cache, dict) else None
    return (isinstance(e, dict) and isinstance(e.get("scores"), dict)
            and all(c in e["scores"] for c in LIVE))


def _author_pool_n(books, cache, author, exclude_title=None):
    """Count the author's books that are actually IN the correction pool
    (build_pairs — i.e. rated AND cached). This is the n the author-correction
    layer sees, not merely the DB row count."""
    try:
        b = books if exclude_title is None else books[books["Book"] != exclude_title]
        df = rm.build_pairs(b, cache)
        if df.empty:
            return 0
        return int((df["Author"] == author).sum())
    except Exception:
        return 0


def _genre_gate_threshold(resid_sd):
    """min(cap, ½ · median conformal half-width). Falls back to the engine's own
    global half-width (1.645·resid_sd) if the residual table is absent, and to
    the bare cap if neither is available. In practice this resolves to the cap."""
    hw = None
    try:
        import intervals
        tbl = intervals.load_residuals(RESIDUALS_PATH)
        if tbl:
            widths = [b.get("half_width") for b in (tbl.get("buckets") or {}).values()
                      if isinstance(b, dict) and b.get("half_width") is not None]
            if widths:
                hw = float(np.median([float(w) for w in widths]))
    except Exception:
        hw = None
    if hw is None:
        try:
            hw = 1.645 * float(resid_sd)
        except (TypeError, ValueError):
            hw = None
    return min(GENRE_REPREDICT_GATE_CAP, 0.5 * hw) if hw is not None else GENRE_REPREDICT_GATE_CAP


def _genre_baseline_wa(df, probe_llm, genre, gw, gcw):
    """Corrected WA of a fixed probe vector through the PURE genre layer
    (reresearch_and_measure.correct_book method 'genre_reg') on a given training
    pool `df`. Uses the real engine function — no math is reimplemented."""
    row = {"Book": "__genre_probe__", "Genre": genre, "Author": "__no_author__"}
    for c in LIVE:
        row["llm_" + c] = float(probe_llm[c])
    df2 = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    corrected = rm.correct_book(df2, len(df2) - 1, "genre_reg")
    return rp._wa_from_components(corrected, genre, gw, gcw)


def _genre_baseline_shift(books, cache, gw, gcw, genre, trigger_title):
    """How much the genre-tier baseline moved when the trigger entered the pool.

    Measured faithfully: the genre-layer corrected WA of a FIXED probe vector
    (the genre pool's mean LLM vector, global-mean fallback), computed with the
    library WITHOUT vs WITH the trigger. Holding the probe fixed isolates the
    baseline shift from probe drift. Returns (|Δ|, wa_pre, wa_post); (0, None,
    None) if it cannot be computed (→ conservatively don't fire the gate)."""
    try:
        df_post = rm.build_pairs(books, cache)
        if df_post.empty:
            return 0.0, None, None
        gp = df_post[df_post["Genre"] == genre]
        src = gp if len(gp) else df_post
        probe_llm = {c: float(src["llm_" + c].mean()) for c in LIVE}
        if any(np.isnan(v) for v in probe_llm.values()):
            return 0.0, None, None
        wa_post = _genre_baseline_wa(df_post, probe_llm, genre, gw, gcw)
        df_pre = rm.build_pairs(books[books["Book"] != trigger_title], cache)
        wa_pre = _genre_baseline_wa(df_pre, probe_llm, genre, gw, gcw)
        return abs(wa_post - wa_pre), wa_pre, wa_post
    except Exception:
        return 0.0, None, None


def _recs_by(con, column, value, exclude_title):
    """Active (done=0) recommendations matching author/genre, excluding the
    trigger title itself (it may still have a done=0 rec row after being read)."""
    cols = ", ".join(f'"{c}"' for c in db_write.FICTION_COMPONENTS)
    rows = con.execute(
        f"SELECT id, title, author, genre, words, {cols} FROM recommendations "
        f"WHERE COALESCE(done,0)=0 AND {column}=? "
        f"AND LOWER(title)<>LOWER(?) ORDER BY id",
        (value, exclude_title),
    ).fetchall()
    return [dict(r) for r in rows]


def _raw_scores_for(title, author, genre, cache, web, web_cache_only, allow_research):
    """Reconstruct a book's raw (pre-correction) component vector exactly as the
    live predict path would: cached LLM scores (or one research call if allowed),
    then policy grounded-overrides when the web layer is available. Returns
    (scores, conf, source) or (None, None, reason) when it must be skipped.

    web_cache_only bounds latency on the on-add path: a grounded override is only
    applied when the book is ALREADY web-cached, so no fresh web call is made
    inside the request. allow_research is False for cohort peers (they should be
    cached; an uncached peer is skipped, not researched synchronously)."""
    if _fully_cached(cache, title):
        e = cache[title]
        mem = {c: float(e["scores"][c]) for c in LIVE}
        conf = e.get("conf", "cache")
    elif allow_research:
        try:
            client = rp.get_client()
            sc, conf, *_ = rp.research_book(title, author, genre, client, cache)
            mem = {c: float(sc[c]) for c in LIVE if c in sc}
            if len(mem) != len(LIVE):
                return None, None, "research-incomplete"
        except Exception as exc:
            return None, None, f"research-failed: {exc}"
    else:
        return None, None, "uncached"

    if web is not None:
        web_ok = True
        if web_cache_only:
            we = getattr(web, "cache", {}).get(title)
            web_ok = (isinstance(we, dict)
                      and all(c in (we.get("scores") or {}) for c in LIVE))
        if web_ok:
            try:
                return hybrid.apply_grounded_overrides(title, author, genre, mem, web=web), conf, "hybrid"
            except Exception:
                pass
    return dict(mem), conf, "memory"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def on_book_added(trigger_title, trigger_author, trigger_genre, trigger_scores=None,
                  *, get_engine, cache=None, web="auto", corr_models="auto",
                  research_trigger=True, web_cache_only=True, dry_run=False,
                  verbose=True):
    """Re-predict the unread cohort whose baseline the finished book just moved.

    Call AFTER the finished book is committed and the engine invalidated, so the
    library and correction pool already reflect n=1.

    get_engine  : callable -> the predict_engine 8-tuple (backend passes
                  _get_engine; tests pass a temp-DB engine).
    cache       : research cache dict (default rp.load_cache()).
    web         : grounded researcher, None to skip overrides, or "auto".
    research_trigger : if the finished book is uncached, research it first so the
                  author correction actually incorporates the new data point.
    web_cache_only   : never make a fresh web call inside this pass (see helper).
    dry_run     : compute + report only; write nothing, log nothing.

    Returns a JSON-serializable report dict (or None if the feature is inert).
    Never raises — callers keep add-book non-fatal.
    """
    try:
        engine = get_engine()
        books, gw, gcw, coeffs, r2, resid_sd, ginfo, upstream = engine
        if cache is None:
            cache = rp.load_cache()
        if web == "auto":
            try:
                web = hybrid._shared_web()
            except Exception:
                web = None

        # --- 1) Ensure the finished book is in the correction pool ------------
        n_after_incl = _author_pool_n(books, cache, trigger_author)
        trigger_cached = _fully_cached(cache, trigger_title)
        researched_now = False
        if not trigger_cached and research_trigger and not dry_run:
            try:
                client = rp.get_client()
                rp.research_book(trigger_title, trigger_author, trigger_genre, client, cache)
                rp.save_cache(cache)
                trigger_cached = _fully_cached(cache, trigger_title)
                researched_now = trigger_cached
                n_after_incl = _author_pool_n(books, cache, trigger_author)
            except Exception:
                pass
        # The author n the cohort's correction now sees, and what it saw before
        # this book entered (the only pool change is the trigger itself).
        n_author_after = n_after_incl
        n_author_before = n_after_incl - (1 if trigger_cached else 0)
        author_is_new = trigger_cached and n_author_before == 0 and n_author_after >= 1

        # corr_models reflect the (possibly newly-cached) trigger, as a fresh
        # prediction today would build them.
        if corr_models == "auto":
            try:
                corr_models = rp.build_corr_models(books, cache)
            except Exception:
                corr_models = None

        # --- 2) Genre gate ---------------------------------------------------
        gate = _genre_gate_threshold(resid_sd)
        shift, wa_pre, wa_post = _genre_baseline_shift(
            books, cache, gw, gcw, trigger_genre, trigger_title)
        gate_fires = shift > gate

        # --- 3) Affected set (author ∪ gated genre) --------------------------
        con = db_backend.connect(db_write.DB)
        con.row_factory = sqlite3.Row
        try:
            author_peers = _recs_by(con, "author", trigger_author, trigger_title)
            genre_peers = _recs_by(con, "genre", trigger_genre, trigger_title)
        finally:
            con.close()
        author_titles = {r["title"] for r in author_peers}
        for r in author_peers:
            r["_reason"] = "author"
        genre_only = [r for r in genre_peers if r["title"] not in author_titles]
        affected = list(author_peers)
        suppressed_genre_peers = []
        capped_genre_peers = []
        if gate_fires:
            # Re-predict the genre-peers a genre shift moves most first (weakest
            # own-author support), cap the rest to bound on-add churn.
            def _author_support(a):
                try:
                    return int((books["Author"] == a).sum())
                except Exception:
                    return 0
            genre_only.sort(key=lambda r: _author_support(r["author"]))
            keep, overflow = genre_only[:MAX_GENRE_PEERS_PER_ADD], genre_only[MAX_GENRE_PEERS_PER_ADD:]
            for r in keep:
                r["_reason"] = "genre"
            affected += keep
            capped_genre_peers = [r["title"] for r in overflow]
        else:
            suppressed_genre_peers = [r["title"] for r in genre_only]

        # --- 4) Re-predict, log delta (old→new), overwrite in place ----------
        rows, skipped = [], []
        for r in affected:
            title = r["title"]
            old = {c: r[c] for c in db_write.FICTION_COMPONENTS}
            old_complete = all(v is not None for v in old.values())
            try:
                old_wa = rp._wa_from_components(old, r["genre"], gw, gcw) if old_complete else None
            except Exception:
                old_wa = None

            raw, conf, src = _raw_scores_for(
                title, r["author"], r["genre"], cache, web,
                web_cache_only=web_cache_only, allow_research=False)
            if raw is None:
                skipped.append({"title": title, "reason": src})
                continue

            res = rp.correct_and_predict(
                title, r["author"], r["genre"], raw, conf, resid_sd,
                books, gw, gcw, cache, corr_models=corr_models)
            new = {c: _clamp(v) for c, v in res["scores"].items()}
            new_wa = rp._wa_from_components(new, r["genre"], gw, gcw)

            old_rank = int((books["WA"] > old_wa).sum() + 1) if old_wa is not None else None
            new_rank = int((books["WA"] > new_wa).sum() + 1)
            drivers = sorted(
                ((c, (new.get(c) or 0) - (old.get(c) or 0)) for c in db_write.FICTION_COMPONENTS),
                key=lambda x: abs(x[1]), reverse=True)[:3]

            if not dry_run:
                # Log the revision BEFORE overwriting the visible row. Tagged so
                # these old→new revision rows are never confused with genuine
                # predicted-vs-actual deltas (precedent: retro_sweep_v1_shrunk).
                # act_* here means "new prediction", so d_wa = new − old = mover.
                if old_wa is not None:
                    meta = {
                        "tag": f"baseline_repredict:{trigger_title}",
                        "pred_genre": r["genre"], "pred_author": r["author"],
                        "n_author": res.get("n_author"), "n_genre": res.get("n_genre"),
                        "analog_src": ("author" if (res.get("n_author") or 0) > 0
                                       else "genre" if (res.get("n_genre") or 0) > 0 else "global"),
                        "corr_method": ("corr_smooth+author_genre" if corr_models else "author_genre"),
                    }
                    db_write.log_delta(title, old, old_wa, new, new_wa,
                                       pred_model=db_write.RESEARCH_MODEL, meta=meta)
                db_write.update_recommendation_scores(title, new)

            rows.append({
                "title": title, "reason": r["_reason"], "source": src,
                "old_wa": round(old_wa, 4) if old_wa is not None else None,
                "new_wa": round(new_wa, 4),
                "d_wa": round(new_wa - old_wa, 4) if old_wa is not None else None,
                "old_rank": old_rank, "new_rank": new_rank,
                "d_rank": (new_rank - old_rank) if old_rank is not None else None,
                "drivers": [{"component": c, "delta": round(d, 2)} for c, d in drivers],
            })

        deltas = [r["d_wa"] for r in rows if r["d_wa"] is not None]
        report = {
            "trigger": {
                "title": trigger_title, "author": trigger_author, "genre": trigger_genre,
                "author_is_new": author_is_new,
                "n_author_before": n_author_before, "n_author_after": n_author_after,
                "trigger_cached": trigger_cached, "researched_now": researched_now,
            },
            "genre_gate": {
                "shift": round(shift, 4), "gate": round(gate, 4), "fired": gate_fires,
                "wa_pre": round(wa_pre, 4) if wa_pre is not None else None,
                "wa_post": round(wa_post, 4) if wa_post is not None else None,
            },
            "affected": rows,
            "suppressed_genre_peers": suppressed_genre_peers,
            "capped_genre_peers": capped_genre_peers,
            "cohort_mean_d_wa": round(float(np.mean(deltas)), 4) if deltas else None,
            "written": 0 if dry_run else len(rows),
            "skipped": skipped,
            "dry_run": dry_run,
        }
        if verbose:
            _print_report(report)
        return report
    except Exception as exc:
        if verbose:
            print(f"  (repredict-on-add skipped: {exc})")
        return None


def _print_report(rep):
    t = rep["trigger"]
    g = rep["genre_gate"]
    tag = "DRY RUN — " if rep["dry_run"] else ""
    print(f"  {tag}baseline re-predict for '{t['title']}' "
          f"({t['author']} · {t['genre']})")
    if t["author_is_new"]:
        print(f"    author baseline: NEW — n {t['n_author_before']}→{t['n_author_after']}"
              + ("  (auto-researched trigger)" if t["researched_now"] else ""))
    else:
        print(f"    author baseline: n {t['n_author_before']}→{t['n_author_after']}")
    print(f"    genre gate: shift {g['shift']:.3f} vs gate {g['gate']:.3f} → "
          f"{'FIRED' if g['fired'] else 'suppressed'}")
    for r in rep["affected"]:
        dwa = f"{r['d_wa']:+.3f}" if r["d_wa"] is not None else "  n/a"
        drv = ", ".join(f"{d['component']} {d['delta']:+.1f}" for d in r["drivers"])
        print(f"    [{r['reason'][:6]:<6}] {r['title'][:34]:<34} "
              f"WA {r['old_wa']}→{r['new_wa']} ({dwa})  {drv}")
    if rep["suppressed_genre_peers"]:
        print(f"    genre-peers NOT churned (gate): {len(rep['suppressed_genre_peers'])} "
              f"({', '.join(rep['suppressed_genre_peers'][:5])}"
              f"{' …' if len(rep['suppressed_genre_peers']) > 5 else ''})")
    if rep.get("capped_genre_peers"):
        print(f"    genre-peers OVER cap (MAX={MAX_GENRE_PEERS_PER_ADD}, deferred): "
              f"{len(rep['capped_genre_peers'])}")
    if rep["skipped"]:
        print(f"    skipped: {', '.join(s['title'] for s in rep['skipped'])}")
    if rep["cohort_mean_d_wa"] is not None:
        print(f"    cohort mean ΔWA = {rep['cohort_mean_d_wa']:+.3f} "
              f"across {len(rep['affected'])} book(s)")


def _main():
    import predict_engine as pe
    ap = argparse.ArgumentParser(description="Dry-run the on-add re-prediction for a finished book.")
    ap.add_argument("title", help="title of a book (used to look up its author+genre in `books`)")
    ap.add_argument("--dry-run", action="store_true", help="compute + report, write nothing (default here)")
    ap.add_argument("--write", action="store_true", help="actually overwrite rows + log deltas")
    ap.add_argument("--no-research", action="store_true", help="do not auto-research an uncached trigger")
    args = ap.parse_args()

    con = db_backend.connect(db_write.DB)
    row = con.execute("SELECT author, genre FROM books WHERE LOWER(title)=LOWER(?)",
                      (args.title,)).fetchone()
    con.close()
    if not row:
        raise SystemExit(f"No rated book titled {args.title!r} in `books`.")
    author, genre = row
    on_book_added(args.title, author, genre, get_engine=lambda: pe.build(source="db"),
                  research_trigger=not args.no_research, dry_run=not args.write)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
