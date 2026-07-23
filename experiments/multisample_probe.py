#!/usr/bin/env python3
"""
multisample_probe.py — free K=2 test of the 'average multiple LLM research passes'
signal. Two independent grounded caches already exist (llm_scores_richer.json +
web_grounded_cache.json); average their component vectors and run the SAME honest
walk-forward pipeline (smoothing + author/genre correction + roll-up) vs the
single-pass baseline. If K=2 already helps, K=4-8 fresh samples would help more.
Zero API cost. Paired bootstrap on ΔMAE. Read-only apart from the report.
"""
import json
import math
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

LIVE = wf.LIVE
BURN = wf.BURN_IN_DEFAULT
SPLITS = wf.SPLIT_MODES
OUT = os.path.join(ROOT, "validation", "multisample_probe.md")
SEED = 99
B = 5000


def _boot(new, base, act):
    rng = random.Random(SEED)
    n = len(act)
    d = []
    for _ in range(B):
        idx = [rng.randrange(n) for _ in range(n)]
        ai = [act[j] for j in idx]
        d.append(np.mean([abs(new[j] - act[j]) for j in idx])
                 - np.mean([abs(base[j] - act[j]) for j in idx]))
    d.sort()
    return d[int(0.025 * B)], d[int(0.975 * B)]


def main():
    books, gw, gcw = db_loader.load_from_db()
    richer = rp.load_cache()
    grounded = json.load(open(os.path.join(ROOT, "web_grounded_cache.json")))
    order, _ = wf.build_order(books)
    row_of = {r["Book"]: r for _, r in books.iterrows()}

    def ok(t):
        return (t in richer and isinstance(richer[t].get("scores"), dict)
                and all(c in richer[t]["scores"] for c in LIVE))

    def rvec(t):
        return {c: float(richer[t]["scores"][c]) for c in LIVE}

    def avec(t):
        r = richer[t]["scores"]
        g = (grounded.get(t, {}) or {}).get("scores", {}) or {}
        return {c: (0.5 * (float(r[c]) + float(g[c])) if c in g else float(r[c]))
                for c in LIVE}

    L = ["# Multi-sample research averaging — free K=2 probe (Phase: new signals)\n"]
    L.append("Average the two independent grounded caches (richer + web_grounded) and run "
             "the honest walk-forward pipeline vs the single-pass baseline. **Negative ΔMAE "
             "with CI below 0 = the averaging signal is real** (and K>2 fresh samples would "
             "help more).\n")
    L.append("| split | n | base MAE | avg2 MAE | ΔMAE [95% CI] | ρ base→avg2 |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    from scipy import stats
    n_both = sum(1 for t in row_of if ok(t) and t in grounded
                 and isinstance(grounded[t].get("scores"), dict))
    for split in SPLITS:
        base, avg, act = [], [], []
        for idx, e in enumerate(order):
            t = e["title"]
            if not ok(t):
                continue
            pool = [pt for pt in wf._pool_titles(order, idx, split) if ok(pt)]
            if len(pool) < BURN:
                continue
            books_pool = books[books["Book"].isin(pool)]
            resid = pe.fit_regression(books_pool)[2]
            corr = rp.build_corr_models(books_pool, richer)
            conf = richer[t].get("conf", "?")
            au, ge = e["author"], e["genre"]
            rb = rp.correct_and_predict(t, au, ge, rvec(t), conf, resid, books_pool,
                                        gw, gcw, richer, corr_models=corr)
            ra = rp.correct_and_predict(t, au, ge, avec(t), conf, resid, books_pool,
                                        gw, gcw, richer, corr_models=corr)
            base.append(rb["wa"])
            avg.append(ra["wa"])
            act.append(float(row_of[t]["WA"]))
        n = len(act)
        bmae = float(np.mean([abs(b - a) for b, a in zip(base, act)]))
        amae = float(np.mean([abs(v - a) for v, a in zip(avg, act)]))
        lo, hi = _boot(avg, base, act)
        rho = float(stats.spearmanr(avg, act)[0])
        L.append(f"| {split} | {n} | {bmae:.3f} | {amae:.3f} | "
                 f"{amae - bmae:+.3f} [{lo:+.3f}, {hi:+.3f}] | {rho:.3f} |")
    L.append("")
    L.append(f"_{n_both}/131 rated books have both passes. This is only K=2 (and the two "
             "passes use different prompts, so it understates what K clean same-prompt "
             "samples would give). A real win here justifies a fresh multi-sample research "
             "run targeted at the high-disagreement / high-headroom components (Ending, "
             "Depth, Plot)._\n")
    md = "\n".join(L) + "\n"
    open(OUT, "w", encoding="utf-8").write(md)
    print(md)
    print(f"  wrote {OUT}")


if __name__ == "__main__":
    main()
