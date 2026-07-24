#!/usr/bin/env python3
"""
hybrid_vs_ensemble.py — the HONEST ship comparison. The live predict default is
NOT memory-only (0.628 walk-forward baseline) — it is the per-component HARD
sourcing hybrid (hybrid_researcher.apply_grounded_overrides: 6 grounded comps +
8 memory). So an ensemble ship must beat the LIVE HYBRID, not memory-only.

Variants (all built from the two existing caches; correction REFIT per fold on
each variant's own pairs — the shippable form):
  memory   richer only                                   (= the 0.628 baseline)
  hybrid   6 grounded comps overridden, 8 memory          (= LIVE default)
  ens25    0.75*richer + 0.25*web_grounded  (all comps)   (proposed ship)
  ens50    0.50*richer + 0.50*web_grounded  (all comps)
Reports WA MAE + rank per split, and the two decisive paired bootstraps:
  hybrid vs memory  (is the live default already better than 0.628?)
  ensemble vs HYBRID (does the ship beat the real current default?)
Zero API cost. Read-only apart from the report.
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
import hybrid_researcher as hyb
from scipy import stats

LIVE = wf.LIVE
BURN = wf.BURN_IN_DEFAULT
SPLITS = wf.SPLIT_MODES
GROUNDED = hyb.grounded_components()          # the live 6 grounded components
OUT = os.path.join(ROOT, "validation", "hybrid_vs_ensemble.md")
SEED = 11
B = 5000
VARIANTS = ["memory", "hybrid", "ens25", "ens50"]


def _boot(pred_a, pred_b, act):
    """CI of MAE(a) - MAE(b), paired over folds (negative => a better)."""
    rng = random.Random(SEED)
    n = len(act)
    d = []
    for _ in range(B):
        idx = [rng.randrange(n) for _ in range(n)]
        a = [act[j] for j in idx]
        d.append(np.mean([abs(pred_a[j] - a[k]) for k, j in enumerate(idx)])
                 - np.mean([abs(pred_b[j] - a[k]) for k, j in enumerate(idx)]))
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

    def vec(t, variant):
        rs, gs = richer[t]["scores"], grounded[t]["scores"]
        if variant == "memory":
            return {c: float(rs[c]) for c in LIVE}
        if variant == "hybrid":
            return {c: float(gs[c]) if c in GROUNDED else float(rs[c]) for c in LIVE}
        w = 0.25 if variant == "ens25" else 0.5
        return {c: (1 - w) * float(rs[c]) + w * float(gs[c]) for c in LIVE}

    caches = {v: {t: {"scores": vec(t, v), "conf": richer[t].get("conf", "?")}
                  for t in richer if ok(t)} for v in VARIANTS}

    results = {}
    for split in SPLITS:
        preds = {v: [] for v in VARIANTS}
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
            for v in VARIANTS:
                ec = caches[v]
                pairs = rm.build_pairs(books_pool, ec)
                corr = rp.build_corr_models(books_pool, ec, pairs=pairs)
                res = rp.correct_and_predict(t, au, ge, dict(ec[t]["scores"]),
                                             ec[t].get("conf", "?"), resid, books_pool,
                                             gw, gcw, ec, corr_models=corr, pairs=pairs)
                preds[v].append(res["wa"])
            act.append(float(row_of[t]["WA"]))
        met = {}
        for v in VARIANTS:
            p = preds[v]
            met[v] = {"mae": float(np.mean([abs(x - a) for x, a in zip(p, act)])),
                      "rho": float(stats.spearmanr(p, act)[0]),
                      "tau": float(stats.kendalltau(p, act)[0])}
        results[split] = (met, preds, act)
        print(f"[{split}] n={len(act)}", file=sys.stderr)

    L = ["# Ensemble vs the LIVE hybrid — honest ship comparison\n"]
    L.append("The live predict default is the per-component **hybrid** (6 grounded + 8 memory), "
             "NOT memory-only. A ship must beat the hybrid. Correction refit per fold on each "
             "variant. Zero API cost.\n")
    L.append(f"Live grounded components: {sorted(GROUNDED)}\n")
    for split in SPLITS:
        met, preds, act = results[split]
        L.append(f"## Split: {split}  (n={len(act)})\n")
        L.append("| variant | WA MAE | Spearman ρ | Kendall τ |\n| --- | --- | --- | --- |")
        for v in VARIANTS:
            L.append(f"| {v} | {met[v]['mae']:.3f} | {met[v]['rho']:.3f} | {met[v]['tau']:.3f} |")
        hm_lo, hm_hi = _boot(preds["hybrid"], preds["memory"], act)
        e25_lo, e25_hi = _boot(preds["ens25"], preds["hybrid"], act)
        e50_lo, e50_hi = _boot(preds["ens50"], preds["hybrid"], act)
        L.append("")
        L.append(f"- hybrid − memory ΔMAE: **{met['hybrid']['mae']-met['memory']['mae']:+.3f}** "
                 f"[{hm_lo:+.3f}, {hm_hi:+.3f}]  {'(hybrid better)' if hm_hi<0 else '(not significant)'}")
        L.append(f"- **ens25 − hybrid** ΔMAE: **{met['ens25']['mae']-met['hybrid']['mae']:+.3f}** "
                 f"[{e25_lo:+.3f}, {e25_hi:+.3f}]  {'✅ ensemble beats hybrid' if e25_hi<0 else '❌ not significant vs hybrid'}")
        L.append(f"- **ens50 − hybrid** ΔMAE: **{met['ens50']['mae']-met['hybrid']['mae']:+.3f}** "
                 f"[{e50_lo:+.3f}, {e50_hi:+.3f}]  {'✅ ensemble beats hybrid' if e50_hi<0 else '❌ not significant vs hybrid'}")
        L.append("")
    md = "\n".join(L) + "\n"
    open(OUT, "w", encoding="utf-8").write(md)
    print(md)
    print(f"  wrote {OUT}")


if __name__ == "__main__":
    main()
