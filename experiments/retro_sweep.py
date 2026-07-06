"""
retro_sweep.py
==============
PHASE 1 of the retro reprediction + recalibration project.

Repredict every READ book under the CURRENT production engine, leave-one-out, and
log each prediction-vs-actual pair into delta_log under a permanent sweep tag, so
an Opus-era calibration corpus can be built WITHOUT polluting the genuine
prospective rows.

The production engine reproduced here (identical to backend/main.py
/api/predict/research):
  1. HYBRID research — memory scores (llm_scores_richer.json, rich Opus prompt)
     with the 6 policy components overridden by web-grounded Opus values
     (web_grounded_cache.json; a fresh goodreads-restricted web_search on a miss).
  2. correlation smoothing (research_predict.build_corr_models / smooth_components).
  3. author+genre hierarchical correction (reresearch_and_measure.correct_book).
  4. WA roll-up from the corrected components (research_predict._wa_from_components).
Separately it records the pure-analog leave-one-out baseline WA (predict_engine
via validate_engine's LOO harness) as `analog_wa`.

NON-NEGOTIABLE LOO PURITY: for each book the analog baseline, the author+genre
correction pool, AND the correlation-smoothing regression are all refit on the
OTHER books only — the target's own scores never enter any pool used to predict
it. Reuses the read-only reference engines (predict_engine / validate_engine /
reresearch_and_measure / research_predict); NO prediction or derived math is
reimplemented here.

SELF-CITATION GUARD: the researcher's web_search is hard-restricted to
goodreads.com, so the owner's own published rankings (jerseymike18.github.io /
the Vercel deployment) cannot be searched or cited. Asserted at startup.

RESUMABLE + IDEMPOTENT: a book already logged under TAG is skipped; every
research result is persisted to its cache per book; an interruption re-runs only
what is left and never double-charges or duplicates a delta_log row (idempotency
key: title + tag).

All delta_log writes go through db_write.log_delta; the sweep tag and analog_wa
ride in its `meta` dict (whitelisted by db_write.DELTA_META_COLUMNS). Everything
else is read-only.

RUN:
  python3 retro_sweep.py --status     # counts + cost preview, NO api calls
  python3 retro_sweep.py              # run / resume the full sweep
  python3 retro_sweep.py --limit N    # smoke test: only the first N to-do books
"""

import argparse
import sqlite3
import sys
import traceback

import numpy as np

import db_loader
import db_write
import validate_engine as ve          # LOO harness over predict_engine (read-only)
import reresearch_and_measure as rm   # correction reference + rich prompt
import research_predict as rp         # production glue: smoothing + correct_and_predict
import hybrid_researcher as hybrid    # production grounded-override path

TAG = "retro_sweep_v1_shrunk"
PRED_MODEL = "claude-opus-4-8"
FC = db_write.FICTION_COMPONENTS      # canonical 14, delta_log order
EST_WEB_COST = 0.12                   # ~$/book for one Opus goodreads web call


def _log(msg):
    print(msg, flush=True)


def _assert_self_citation_guard():
    """Refuse to run unless the researcher's web_search is goodreads-only, so the
    owner's own published rankings can never be fetched or cited."""
    dom = list((getattr(rp, "WEB_SEARCH_TOOL", {}) or {}).get("allowed_domains") or [])
    assert dom == ["goodreads.com"], (
        f"SELF-CITATION GUARD FAILED: web tool allowed_domains={dom}, "
        f"expected exactly ['goodreads.com']. Refusing to run.")
    web = hybrid._shared_web()
    wdom = list((getattr(web, "search_tool", {}) or {}).get("allowed_domains") or [])
    assert wdom == ["goodreads.com"], (
        f"SELF-CITATION GUARD FAILED: shared web researcher domains={wdom}. "
        f"Refusing to run.")
    return web


def _done_titles():
    """Titles already logged under this sweep tag (idempotency / resume)."""
    con = sqlite3.connect(db_write.DB)
    try:
        rows = [r[0] for r in con.execute(
            "SELECT title FROM delta_log WHERE tag=?", (TAG,))]
    finally:
        con.close()
    return set(rows)


def _act_scores(row):
    out = {}
    for c in FC:
        v = row.get(c)
        if v is not None and not (isinstance(v, float) and np.isnan(v)):
            out[c] = float(v)
    return out


def _memory_scores(title, author, genre, cache):
    """The 8 memory-sourced components' source vector (all 14 from the rich
    Opus prompt; the hybrid keeps 8 and overrides 6). Cache-first; a miss makes
    ONE Opus call and persists to llm_scores_richer.json (research_predict cache)."""
    e = cache.get(title)
    if isinstance(e, dict) and isinstance(e.get("scores"), dict) \
            and all(c in e["scores"] for c in rm.LIVE):
        return {c: float(e["scores"][c]) for c in rm.LIVE}, e.get("conf", "cache")
    # Miss (e.g. Endymion): research with the production richer prompt.
    client = rp.get_client()
    scores, conf, *_ = rp.research_book(title, author, genre, client, cache,
                                        allowed_genres=None)
    return {c: float(scores[c]) for c in rm.LIVE if c in scores}, conf


def _predict_one_book(row, books, gw, gcw, cache, web):
    """Full production-faithful, LOO-pure prediction for one read book.
    Returns (res, meta, analog_wa) where res is correct_and_predict's dict."""
    i = row.name
    title, author, genre = row["Book"], row["Author"], row["Genre"]
    books_loo = books.drop(i)                      # target removed from ALL pools

    # 1. HYBRID research (memory + 6 grounded overrides), exactly as production.
    mem_scores, conf = _memory_scores(title, author, genre, cache)
    try:
        hyb_scores = hybrid.apply_grounded_overrides(
            title, author, genre, mem_scores, web=web)
        sourcing = "hybrid"
    except Exception as e:                          # production falls back to memory
        hyb_scores = dict(mem_scores)
        sourcing = "memory"
        _log(f"    [warn] grounding failed for {title!r}: {e} — using memory scores")

    # 2. Analog leave-one-out baseline (pure predict_engine, no LLM).
    coeffs, _r2, resid_sd, ginfo, upstream = ve.fit_on(books_loo)
    analog_wa, _analog_est = ve.predict_one(
        row, books_loo, gw, gcw, coeffs, resid_sd, ginfo, upstream)

    # 3. Correlation-smoothing models refit WITHOUT the target (LOO).
    corr_models = rp.build_corr_models(books_loo, cache)

    # 4. Corrected research prediction (smoothing + author_genre correction; the
    #    correction pool excludes the target by construction).
    res = rp.correct_and_predict(
        title, author, genre, hyb_scores, conf, resid_sd,
        books_loo, gw, gcw, cache, corr_models=corr_models)

    # 5. Mechanism metadata (analog_src, n_author/n_genre = blend weights,
    #    correction split, CI, conf) — re-derived from the SAME LOO inputs.
    words = row.get("Words")
    meta = rp.build_prediction_meta(
        title, author, genre, words, res["wa"], resid_sd,
        books_loo, gw, gcw, cache, corr_models=corr_models)
    meta["tag"] = TAG
    meta["analog_wa"] = round(float(analog_wa), 4)
    meta["sourcing"] = sourcing        # not a delta col; dropped by log_delta whitelist
    return res, meta, float(analog_wa)


def _cost_preview(todo_titles, books, cache, web):
    need_web, need_mem = [], []
    for t in todo_titles:
        e = web.cache.get(t)
        if not (isinstance(e, dict) and all(c in e.get("scores", {}) for c in rm.LIVE)):
            need_web.append(t)
        if t not in cache:
            need_mem.append(t)
    return need_web, need_mem


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--status", action="store_true",
                    help="print progress + cost preview and exit (no API calls)")
    ap.add_argument("--limit", type=int, default=None,
                    help="only process the first N to-do books (smoke test)")
    args = ap.parse_args()

    web = _assert_self_citation_guard()
    books, gw, gcw = db_loader.load_from_db()
    cache = rp.load_cache()

    already = _done_titles()
    todo_idx = [i for i in books.index.tolist()
                if books.loc[i, "Book"] not in already]
    todo_titles = [books.loc[i, "Book"] for i in todo_idx]
    need_web, need_mem = _cost_preview(todo_titles, books, cache, web)

    _log("=" * 72)
    _log(f"RETRO SWEEP  tag={TAG}  model={PRED_MODEL}")
    _log("=" * 72)
    _log(f"  read books total     : {len(books)}")
    _log(f"  already logged (tag) : {len(already)}")
    _log(f"  to do this run       : {len(todo_idx)}"
         + (f"  (capped at {args.limit})" if args.limit else ""))
    _log(f"  live web calls needed: ~{len(need_web)}  "
         f"(~${EST_WEB_COST * len(need_web):.2f})")
    _log(f"  memory research needed: {len(need_mem)}"
         + (f"  {need_mem}" if need_mem else ""))
    _log("=" * 72)
    if args.status:
        return 0
    if not todo_idx:
        _log("Nothing to do — sweep already complete for this tag.")
        return 0

    if args.limit:
        todo_idx = todo_idx[:args.limit]

    n = len(todo_idx)
    ok = 0
    for k, i in enumerate(todo_idx, 1):
        row = books.loc[i]
        title = row["Book"]
        try:
            res, meta, analog_wa = _predict_one_book(row, books, gw, gcw, cache, web)
            act = _act_scores(row)
            act_wa = float(row["WA"])
            db_write.log_delta(title, res["scores"], res["wa"], act, act_wa,
                               pred_model=PRED_MODEL, meta=meta)
            rp.save_cache(cache)       # persist any newly researched memory scores
            ok += 1
            d = act_wa - res["wa"]
            _log(f"[{k:>3}/{n}] {title[:34]:<34} "
                 f"pred={res['wa']:.2f} analog={analog_wa:.2f} act={act_wa:.2f} "
                 f"d={d:+.2f}  n_a={meta.get('n_author')} n_g={meta.get('n_genre')} "
                 f"src={meta.get('analog_src')} [{meta.get('sourcing')}]")
        except Exception as e:
            _log(f"[{k:>3}/{n}] {title[:34]:<34} ERROR: {e}")
            traceback.print_exc()
            # keep going; the book stays un-logged and will retry on the next run

    _log("=" * 72)
    _log(f"SWEEP RUN COMPLETE — {ok}/{n} logged this run; "
         f"{len(_done_titles())}/{len(books)} total under tag {TAG}")
    _log("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
