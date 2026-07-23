#!/usr/bin/env python3
"""
branch_a_robustness.py — is any Branch-A candidate a REAL win, or noise?
For each split, for the candidates closest to baseline:
  * paired bootstrap of per-fold ΔMAE (candidate − baseline): CI vs 0;
  * the ORACLE convex blend w*·candidate + (1−w*)·baseline (w* chosen on the
    SAME folds — an optimistic upper bound), its MAE, and a bootstrap CI on its
    gain over baseline. If even the oracle-tuned blend's gain CI straddles 0, the
    candidate adds nothing.
Reads validation/branch_a_preds.json (written by branch_a_eval.py).
"""
import json
import os
import random
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREDS = os.path.join(ROOT, "validation", "branch_a_preds.json")
OUT = os.path.join(ROOT, "validation", "branch_a_robustness.md")
SEED = 12345
B = 5000
CANDS = ["ridge_llm_emb", "ridge_llm_emb_time", "knn_emb", "blend_base_knn"]


def _arr(xs):
    return np.array([np.nan if v is None else float(v) for v in xs])


def main():
    preds = json.load(open(PREDS))
    L = ["# Branch A — robustness (paired bootstrap + oracle blend)\n"]
    L.append(f"Paired bootstrap over folds (B={B}). ΔMAE = candidate − baseline "
             "(negative = better). Oracle blend picks w on the same folds (optimistic). "
             "**A candidate only 'wins' if a CI is entirely below 0.**\n")
    for split, d in preds.items():
        a = _arr(d["actual"])
        base = _arr(d["baseline"])
        L.append(f"## Split: {split}\n")
        L.append("| candidate | base MAE | cand MAE | ΔMAE [95% CI] | oracle w* | blend MAE | blend gain [95% CI] |")
        L.append("| --- | --- | --- | --- | --- | --- | --- |")
        for cand in CANDS:
            c = _arr(d[cand])
            mask = ~np.isnan(a) & ~np.isnan(base) & ~np.isnan(c)
            aa, bb, cc = a[mask], base[mask], c[mask]
            n = len(aa)
            base_mae = np.mean(np.abs(bb - aa))
            cand_mae = np.mean(np.abs(cc - aa))

            # oracle convex blend
            ws = np.linspace(0, 1, 101)
            blend_maes = [np.mean(np.abs((w * cc + (1 - w) * bb) - aa)) for w in ws]
            wi = int(np.argmin(blend_maes))
            wstar, blend_mae = ws[wi], blend_maes[wi]

            rng = random.Random(f"{SEED}:{split}:{cand}")
            dmae, dblend = [], []
            for _ in range(B):
                idx = [rng.randrange(n) for _ in range(n)]
                ai, bi, ci = aa[idx], bb[idx], cc[idx]
                dmae.append(np.mean(np.abs(ci - ai)) - np.mean(np.abs(bi - ai)))
                dblend.append(np.mean(np.abs((wstar * ci + (1 - wstar) * bi) - ai))
                              - np.mean(np.abs(bi - ai)))
            dmae.sort(); dblend.sort()
            ci = (dmae[int(0.025 * B)], dmae[int(0.975 * B)])
            cib = (dblend[int(0.025 * B)], dblend[int(0.975 * B)])
            L.append(f"| {cand} | {base_mae:.3f} | {cand_mae:.3f} | "
                     f"{cand_mae - base_mae:+.3f} [{ci[0]:+.3f}, {ci[1]:+.3f}] | "
                     f"{wstar:.2f} | {blend_mae:.3f} | "
                     f"{blend_mae - base_mae:+.3f} [{cib[0]:+.3f}, {cib[1]:+.3f}] |")
        L.append("")
    md = "\n".join(L) + "\n"
    open(OUT, "w", encoding="utf-8").write(md)
    print(md)
    print(f"  wrote {OUT}")


if __name__ == "__main__":
    main()
