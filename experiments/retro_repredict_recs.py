"""
retro_repredict_recs.py
=======================
PHASE 3 — repredict the recommendations (TBR) table under the FINAL engine
(post-Phase-2: corrections retired, so this is the current coded engine —
hybrid Opus grounded research + correlation smoothing + author_genre correction),
writing the new component scores in place. WA / interval / rank are DERIVED on
read (the table stores only components), so only the 14 components are written.

These are unread TBR books, NOT in the rated library, so there is no leave-one-out
concern — each is predicted against the full 127-book library exactly as the live
/api/predict/research path does.

SAFETY / FAITHFULNESS
  - Snapshot: every scoped row's OLD components + derived old WA are saved to
    calibration/recs_pre_reswept_<date>.json BEFORE any write (no silent
    overwrite; the file is the comparison side-table).
  - Writes go through db_write.update_recommendation_scores (validated, in place).
  - Self-citation guard: web research is goodreads-only (asserted).
  - Resumable: research results persist to their caches per book; a re-run
    re-researches nothing already cached and simply re-writes (idempotent).
  - Parallel research (thread-safe web researcher), then SERIAL writes so the
    SQLite writer never contends.

RUN
  python3 retro_repredict_recs.py --status          # cost preview, no API calls
  python3 retro_repredict_recs.py [--scope active]  # run (active=undone only)
  python3 retro_repredict_recs.py --limit N          # smoke test on N rows
"""

import argparse
import json
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import db_loader
import db_write
import predict_engine as pe
import research_predict as rp
import hybrid_researcher as hybrid

FC = db_write.FICTION_COMPONENTS
EST_WEB_COST = 0.12
SNAP_DIR = "calibration"


def _log(m):
    print(m, flush=True)


def _assert_guard():
    dom = list((getattr(rp, "WEB_SEARCH_TOOL", {}) or {}).get("allowed_domains") or [])
    assert dom == ["goodreads.com"], f"self-citation guard failed: {dom}"
    web = hybrid._shared_web()
    wdom = list((getattr(web, "search_tool", {}) or {}).get("allowed_domains") or [])
    assert wdom == ["goodreads.com"], f"self-citation guard failed (web): {wdom}"
    return web


def load_recs(scope):
    con = sqlite3.connect(db_write.DB)
    con.row_factory = sqlite3.Row
    where = "WHERE COALESCE(done,0)=0" if scope == "active" else ""
    cols = ", ".join(f'"{c}"' for c in FC)
    rows = [dict(r) for r in con.execute(
        f"SELECT id, title, author, genre, words, COALESCE(done,0) done, {cols} "
        f"FROM recommendations {where} ORDER BY id").fetchall()]
    con.close()
    return rows


def old_scores_of(row):
    return {c: row[c] for c in FC}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scope", choices=["active", "all"], default="active")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--date", default=None, help="snapshot date stamp (YYYY-MM-DD)")
    ap.add_argument("--only-incomplete", action="store_true",
                    help="finish pass: only (re)do active recs not fully "
                         "web-grounded (unfinished + memory-fallback rows)")
    args = ap.parse_args()

    web = _assert_guard()
    books, gw, gcw, coeffs, r2, resid_sd, ginfo, upstream = pe.build()
    cache = rp.load_cache()
    corr_models = rp.build_corr_models(books, cache)

    recs = load_recs(args.scope)
    # Cost preview.
    LIVE = rp.LIVE
    def web_full(t):
        e = web.cache.get(t)
        return isinstance(e, dict) and all(c in e.get("scores", {}) for c in LIVE)
    if args.only_incomplete:
        before = len(recs)
        recs = [r for r in recs if not web_full(r["title"])]
        _log(f"  --only-incomplete: {len(recs)}/{before} rows not fully "
             f"web-grounded (unfinished + memory-fallback)")
    need_web = [r for r in recs if not web_full(r["title"])]
    need_mem = [r for r in recs if r["title"] not in cache]
    _log("=" * 74)
    _log(f"PHASE 3 — repredict recommendations  (scope={args.scope})")
    _log("=" * 74)
    _log(f"  rows in scope        : {len(recs)}")
    _log(f"  live web calls needed: ~{len(need_web)}  (~${EST_WEB_COST*len(need_web):.0f})")
    _log(f"  memory calls needed  : ~{len(need_mem)}  (~${0.01*len(need_mem):.0f})")
    _log(f"  already fully cached : {len(recs)-len(need_web)}  (free)")
    _log("=" * 74)
    if args.status:
        return 0

    if args.limit:
        recs = recs[:args.limit]

    # --- Snapshot OLD predictions BEFORE any write ----------------------------
    date = args.date or "2026-07-05"
    os.makedirs(SNAP_DIR, exist_ok=True)
    snap_path = os.path.join(SNAP_DIR, f"recs_pre_reswept_{date}.json")
    if os.path.exists(snap_path):
        # Preserve the ORIGINAL true-old snapshot across finish/resume passes —
        # never overwrite it with already-repredicted values.
        snapshot = json.load(open(snap_path))["rows"]
        _log(f"Loaded existing snapshot ({len(snapshot)} rows) → {snap_path} "
             f"(preserving true-old values)\n")
    else:
        snapshot = []
        for r in recs:
            old = old_scores_of(r)
            try:
                old_wa = rp._wa_from_components(old, r["genre"], gw, gcw) \
                    if all(v is not None for v in old.values()) else None
            except Exception:
                old_wa = None
            snapshot.append({"title": r["title"], "genre": r["genre"],
                             "author": r["author"], "old_scores": old,
                             "old_wa": round(old_wa, 4) if old_wa is not None else None})
        with open(snap_path, "w") as f:
            json.dump({"scope": args.scope, "n": len(snapshot), "rows": snapshot}, f, indent=2)
        _log(f"Snapshot of {len(snapshot)} old predictions → {snap_path}\n")

    # --- Parallel research + prediction (no writes here) ----------------------
    save_lock = threading.Lock()
    results = {}   # title -> res dict

    def predict_one(r):
        title, author, genre = r["title"], r["author"], r["genre"]
        e = cache.get(title)
        if isinstance(e, dict) and isinstance(e.get("scores"), dict) \
                and all(c in e["scores"] for c in LIVE):
            mem = {c: float(e["scores"][c]) for c in LIVE}
            conf = e.get("conf", "cache")
        else:
            client = rp.get_client()
            sc, conf, *_ = rp.research_book(title, author, genre, client, cache,
                                            allowed_genres=None)
            mem = {c: float(sc[c]) for c in LIVE if c in sc}
        try:
            hyb = hybrid.apply_grounded_overrides(title, author, genre, mem, web=web)
            src = "hybrid"
        except Exception:
            hyb, src = dict(mem), "memory"
        res = rp.correct_and_predict(title, author, genre, hyb, conf, resid_sd,
                                     books, gw, gcw, cache, corr_models=corr_models)
        res["_sourcing"] = src
        return title, res

    n = len(recs)
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(predict_one, r): r for r in recs}
        for fut in as_completed(futs):
            r = futs[fut]
            try:
                title, res = fut.result()
                results[title] = res
            except Exception as e:
                _log(f"  [error] {r['title'][:40]!r}: {e}")
            done += 1
            if done % 25 == 0:
                with save_lock:
                    rp.save_cache(cache)
                _log(f"  researched {done}/{n} ...")
    with save_lock:
        rp.save_cache(cache)
    _log(f"Research complete: {len(results)}/{n} predicted.\n")

    # --- Serial writes --------------------------------------------------------
    written = 0
    for r in recs:
        res = results.get(r["title"])
        if not res:
            continue
        # Clamp to the defined 0-10 range: the author_genre correction can
        # extrapolate a hair past 10 for a book the model scored near-max, which
        # the validated writer (correctly) rejects. Clamping preserves the score.
        scores = {c: min(10.0, max(0.0, float(v)))
                  for c, v in res["scores"].items()}
        if db_write.update_recommendation_scores(r["title"], scores):
            written += 1
    _log(f"Wrote {written}/{len(results)} repredictions.\n")

    # --- Movers report --------------------------------------------------------
    old_wa = {s["title"]: s["old_wa"] for s in snapshot}
    movers = []
    for title, res in results.items():
        ow, nw = old_wa.get(title), res["wa"]
        if ow is None:
            continue
        movers.append((title, ow, nw, nw - ow, res))
    movers.sort(key=lambda x: abs(x[3]), reverse=True)
    deltas = [m[3] for m in movers]
    _log("=" * 74)
    _log(f"OLD vs NEW WA  ({len(movers)} comparable)")
    _log("=" * 74)
    if deltas:
        import numpy as np
        _log(f"  mean |ΔWA| = {np.mean(np.abs(deltas)):.3f}   "
             f"mean ΔWA = {np.mean(deltas):+.3f}   "
             f"max |ΔWA| = {max(abs(d) for d in deltas):.2f}")
    snap_old = {s["title"]: s["old_scores"] for s in snapshot}
    _log(f"\n  TOP 20 MOVERS (|ΔWA|):")
    _log(f"  {'title':<34}{'old':>6}{'new':>6}{'ΔWA':>7}  driver (largest component shifts)")
    for title, ow, nw, d, res in movers[:20]:
        old = snap_old.get(title, {})
        comp_d = sorted(((c, (res['scores'].get(c) or 0) - (old.get(c) or 0))
                         for c in FC), key=lambda x: abs(x[1]), reverse=True)[:3]
        drv = ", ".join(f"{c} {dv:+.1f}" for c, dv in comp_d)
        _log(f"  {title[:33]:<34}{ow:>6.2f}{nw:>6.2f}{d:>+7.2f}  {drv}")
    _log("=" * 74)
    _log("Driver note: Phase 2 retired the constant corrections and the analog "
         "library is unchanged, so movement is the NEW grounded research vs the "
         "prior stored prediction (see per-book component shifts above).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
