#!/usr/bin/env python3
"""
isotonic_recal.py — Phase 4.1: isotonic recalibration of the baseline's honest
walk-forward out-of-fold (OOF) predictions. A monotone map pred→actual corrects
systematic regression-to-the-mean compression + bias WITHOUT touching the model.

Because the map is monotone it CANNOT change rank order, so ρ/τ are (essentially)
invariant — this is an MAE-only play. Evaluated two ways per split:
  * wf-isotonic  — for each book, fit isotonic on ONLY the OOF pairs of earlier
    books (past-only; identity until MIN_FIT past pairs exist). The honest,
    walk-forward-authority number that decides SHIP / NO-GO.
  * loo-isotonic — fit on all-but-one (uses future too); an upper-bound reference
    for how much calibration could help with more data.
Plus a calibration diagnostic (OLS slope/bias) and a paired bootstrap on ΔMAE.
Reads committed baseline folds. Read-only apart from the markdown report.
"""
import json
import os
import random
import sys

import numpy as np
from sklearn.isotonic import IsotonicRegression

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FOLDS = {
    "time": os.path.join(ROOT, "validation", "walkforward_folds.jsonl"),
    "author": os.path.join(ROOT, "validation", "splits", "author", "walkforward_folds.jsonl"),
    "series": os.path.join(ROOT, "validation", "splits", "series", "walkforward_folds.jsonl"),
}
OUT = os.path.join(ROOT, "validation", "isotonic_recal.md")
MIN_FIT = 20
SEED = 4242
B = 5000


def _load(path):
    """Ordered (by position) lists of honest OOF pred + actual."""
    rows = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("skip"):
            continue
        rows.append((r["position"], r["variants"]["honest"]["wa"], r["actual_wa"]))
    rows.sort()
    pred = np.array([p for _, p, _ in rows])
    act = np.array([a for _, _, a in rows])
    return pred, act


def _iso_map(px, py, x):
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(px, py)
    return float(iso.predict([x])[0])


def _wf_isotonic(pred, act):
    out = np.array(pred, dtype=float)
    for i in range(len(pred)):
        if i >= MIN_FIT:
            out[i] = _iso_map(pred[:i], act[:i], pred[i])
    return out


def _wf_linear(pred, act):
    """Minimal monotone recalibration: affine act~a+b*pred fit on PAST OOF pairs
    (Platt-style), applied walk-forward. 2 params -> far less overfit than the
    flexible isotonic map; the fair floor for 'does any monotone recal help?'."""
    out = np.array(pred, dtype=float)
    for i in range(len(pred)):
        if i >= MIN_FIT:
            b, a = np.polyfit(pred[:i], act[:i], 1)
            out[i] = min(10.0, max(0.0, a + b * pred[i]))
    return out


def _loo_isotonic(pred, act):
    out = np.array(pred, dtype=float)
    n = len(pred)
    for i in range(n):
        idx = [j for j in range(n) if j != i]
        out[i] = _iso_map(pred[idx], act[idx], pred[i])
    return out


def _mae(p, a):
    return float(np.mean(np.abs(p - a)))


def _boot_ci(new, base, act):
    rng = random.Random(SEED)
    n = len(act)
    d = []
    for _ in range(B):
        idx = [rng.randrange(n) for _ in range(n)]
        ai = act[idx]
        d.append(_mae(new[idx], ai) - _mae(base[idx], ai))
    d.sort()
    return d[int(0.025 * B)], d[int(0.975 * B)]


def main():
    L = ["# Isotonic Recalibration (Phase 4.1)\n"]
    L.append("Monotone map on the honest OOF predictions. Rank order is preserved by "
             "construction (MAE-only). **wf-isotonic is the authority; loo-isotonic is an "
             f"upper-bound reference.** Warm-up: identity until {MIN_FIT} past pairs.\n")
    L.append("Calibration diagnostic: OLS `actual ~ slope·pred + intercept`. "
             "slope > 1 ⇒ predictions compressed toward the mean (under-dispersed); "
             "slope < 1 ⇒ over-dispersed (over-shoots the extremes). Either is a monotone "
             "miscalibration a recal map could in principle fix.\n")
    L.append("| split | n | slope | intc | base MAE | wf-iso Δ [95% CI] | wf-linear Δ [95% CI] | loo-iso Δ |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for split, path in FOLDS.items():
        if not os.path.exists(path):
            continue
        pred, act = _load(path)
        n = len(act)
        slope, intc = np.polyfit(pred, act, 1)
        base_mae = _mae(pred, act)
        wf = _wf_isotonic(pred, act)
        wl = _wf_linear(pred, act)
        loo = _loo_isotonic(pred, act)
        wf_mae, wl_mae, loo_mae = _mae(wf, act), _mae(wl, act), _mae(loo, act)
        ilo, ihi = _boot_ci(wf, pred, act)
        llo, lhi = _boot_ci(wl, pred, act)
        L.append(f"| {split} | {n} | {slope:.3f} | {intc:+.3f} | {base_mae:.3f} | "
                 f"{wf_mae - base_mae:+.3f} [{ilo:+.3f}, {ihi:+.3f}] | "
                 f"{wl_mae - base_mae:+.3f} [{llo:+.3f}, {lhi:+.3f}] | "
                 f"{loo_mae - base_mae:+.3f} |")
    L.append("")
    L.append("_A SHIP needs a wf Δ CI entirely below 0. wf-linear (2 params) is the "
             "minimal monotone recal; if even it doesn't win, flexible isotonic "
             "overfitting is not the only reason — there is simply no monotone recal gain._\n")
    md = "\n".join(L) + "\n"
    open(OUT, "w", encoding="utf-8").write(md)
    print(md)
    print(f"  wrote {OUT}")


if __name__ == "__main__":
    main()
