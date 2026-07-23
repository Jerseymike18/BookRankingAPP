#!/usr/bin/env python3
"""
ensemble_eval.py — free 2-method research ensemble (Phase: new signals).
Averages the two INDEPENDENT research vectors already on disk (llm_scores_richer =
model-knowledge, web_grounded = web-search) at several weights and runs the full
honest walk-forward pipeline (smoothing + author/genre correction + roll-up),
with the correction REFIT on the ensembled pairs per fold (the shippable version).
w=0 is the current baseline (richer only); w=1 is web-grounded only.

Reports WA MAE + Spearman + Kendall per weight per split, a paired bootstrap of
ΔMAE vs baseline, and a per-component estimation-MAE breakdown at w=0.5.
Zero API cost (both caches exist). Read-only apart from the report.
"""
import json
import os
import random
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_BACKEND", "sqlite")

import walkforward as wf
import db_loader
import predict_engine as pe
import research_predict as rp
import reresearch_and_measure as rm
from scipy import stats

LIVE = wf.LIVE
BURN = wf.BURN_IN_DEFAULT
SPLITS = wf.SPLIT_MODES
WEIGHTS = [0.0, 0.25, 0.5, 0.75, 1.0]
OUT = os.path.join(ROOT, "validation", "ensemble_results.md")
SEED = 7
B = 5000


def _boot(pred_w, pred_base, act):
    rng = random.Random(SEED)
    n = len(act)
    d = []
    for _ in range(B):
        idx = [rng.randrange(n) for _ in range(n)]
        a = [act[j] for j in idx]
        d.append(np.mean([abs(pred_w[j] - a[k]) for k, j in enumerate(idx)])
                 - np.mean([abs(pred_base[j] - a[k]) for k, j in enumerate(idx)]))
    d.sort()
    return d[int(0.025 * B)], d[int(0.975 * B)]


def main():
    books, gw, gcw = db_loader.load_from_db()
    richer = rp.load_cache()
    grounded = json.load(open(os.path.join(ROOT, "web_grounded_cache.json")))
    order, _ = wf.build_order(books)
    row_of = {r["Book"]: r for _, r in books.iterrows()}

    def ok(t):
        return (t in richer and t in grounded and t in row_of
                and isinstance(richer[t].get("scores"), dict)
                and isinstance(grounded[t].get("scores"), dict)
                and all(c in richer[t]["scores"] and c in grounded[t]["scores"] for c in LIVE))

    # Pre-build the ensemble caches (full library) once per weight.
    ecache = {}
    for w in WEIGHTS:
        d = {}
        for t in richer:
            if not ok(t):
                continue
            rs, gs = richer[t]["scores"], grounded[t]["scores"]
            d[t] = {"scores": {c: (1 - w) * float(rs[c]) + w * float(gs[c]) for c in LIVE},
                    "conf": richer[t].get("conf", "?")}
        ecache[w] = d

    results = {}          # split -> {w -> {mae,rho,tau,n,preds}}
    comp_abs = {0.0: {c: [] for c in LIVE}, 0.5: {c: [] for c in LIVE}}  # per-component (time split)
    for split in SPLITS:
        preds = {w: [] for w in WEIGHTS}
        act = []
        for idx, e in enumerate(order):
            t = e["title"]
            if not ok(t):
                continue
            pool = [pt for pt in wf._pool_titles(order, idx, split) if ok(pt)]
            if len(pool) < BURN:
                continue
            books_pool = books[books["Book"].isin(pool)]
            resid = pe.fit_regression(books_pool)[2]
            au, ge = e["author"], e["genre"]
            for w in WEIGHTS:
                ec = ecache[w]
                pairs = rm.build_pairs(books_pool, ec)
                corr = rp.build_corr_models(books_pool, ec, pairs=pairs)
                vec = {c: float(ec[t]["scores"][c]) for c in LIVE}
                res = rp.correct_and_predict(t, au, ge, vec, ec[t].get("conf", "?"),
                                             resid, books_pool, gw, gcw, ec,
                                             corr_models=corr, pairs=pairs)
                preds[w].append(res["wa"])
                if split == "time" and w in (0.0, 0.5):
                    for c in LIVE:
                        a = row_of[t][c]
                        if a is None or (isinstance(a, float) and np.isnan(a)):
                            continue
                        if c in ("Depth2", "Integration", "Originality") and a in (0, 0.0):
                            continue
                        comp_abs[w][c].append(abs(res["scores"][c] - float(a)))
            act.append(float(row_of[t]["WA"]))
        res_w = {}
        for w in WEIGHTS:
            p = preds[w]
            mae = float(np.mean([abs(x - a) for x, a in zip(p, act)]))
            res_w[w] = {"mae": mae, "rho": float(stats.spearmanr(p, act)[0]),
                        "tau": float(stats.kendalltau(p, act)[0]), "n": len(act), "preds": p}
        results[split] = (res_w, preds, act)
        print(f"[{split}] done, n={len(act)}", file=sys.stderr)

    # ---- report ----
    L = ["# Two-method research ensemble — full evaluation (Phase: new signals)\n"]
    L.append("Ensemble = (1−w)·richer[model-knowledge] + w·web_grounded[web-search], both "
             "already cached (zero API cost). Full honest walk-forward with the correction "
             "REFIT on the ensembled pairs per fold. **w=0 is today's baseline; w=1 is "
             "web-grounded only.** Lower MAE / higher ρ,τ better.\n")
    for split in SPLITS:
        res_w, preds, act = results[split]
        base = res_w[0.0]["mae"]
        L.append(f"## Split: {split}  (n={res_w[0.0]['n']})\n")
        L.append("| weight w | WA MAE | Δ vs base [95% CI] | Spearman ρ | Kendall τ |")
        L.append("| --- | --- | --- | --- | --- |")
        for w in WEIGHTS:
            m = res_w[w]
            if w == 0.0:
                dv = "— (baseline)"
            else:
                lo, hi = _boot(preds[w], preds[0.0], act)
                star = " ⭐" if hi < 0 else ""
                dv = f"{m['mae']-base:+.3f} [{lo:+.3f}, {hi:+.3f}]{star}"
            L.append(f"| {w:.2f} | {m['mae']:.3f} | {dv} | {m['rho']:.3f} | {m['tau']:.3f} |")
        L.append("")
    # per-component at w=0.5 (time split)
    L.append("## Per-component estimation MAE — baseline (w=0) vs ensemble (w=0.5), time split\n")
    L.append("| component | base MAE | ensemble MAE | Δ |\n| --- | --- | --- | --- |")
    rows = []
    for c in LIVE:
        b = comp_abs[0.0][c]; e = comp_abs[0.5][c]
        if not b or not e:
            continue
        bm, em = float(np.mean(b)), float(np.mean(e))
        rows.append((c, bm, em, em - bm))
    for c, bm, em, d in sorted(rows, key=lambda r: r[3]):
        L.append(f"| {c} | {bm:.3f} | {em:.3f} | {d:+.3f} |")
    L.append("")
    L.append("_⭐ = ensemble beats baseline with the whole ΔMAE 95% CI below 0. The cold-start "
             "author/series splits are the ones that matter for a ship decision._\n")
    md = "\n".join(L) + "\n"
    open(OUT, "w", encoding="utf-8").write(md)
    print(md)
    print(f"  wrote {OUT}")


if __name__ == "__main__":
    main()
