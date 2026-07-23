#!/usr/bin/env python3
"""
component_headroom.py — cross Phase 2.1 (noise floor) x 2.2 (estimation error).
Per component: the engine's estimation MAE vs the test-retest FLOOR (the most the
component can be improved), and each component's weight in WA — so Branch A can
target components that are both high-impact AND far from their floor, and skip
ones that are already as good as Michael's own re-rating consistency.
Read-only; writes validation/component_headroom.md.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_BACKEND", "sqlite")

import db_loader
import db_write

FOLDS = os.path.join(ROOT, "validation", "walkforward_folds.jsonl")
OUT = os.path.join(ROOT, "validation", "component_headroom.md")

CAT_OF = {
    "Plot": "Story", "Entertainment": "Story", "Action": "Story", "Ending": "Story",
    "Depth": "Character", "Emotional Impact": "Character", "Motivations": "Character",
    "Prose": "Aesthetics", "Narration": "Aesthetics",
    "Insights": "Theme", "Thought-Provokingness": "Theme",
    "Depth2": "Worldbuilding", "Integration": "Worldbuilding", "Originality": "Worldbuilding",
}
COMPS = list(CAT_OF)
WB = {"Depth2", "Integration", "Originality"}


def _eff_weight(comp, genre, gw, gcw):
    cat = CAT_OF[comp]
    cw = gw.get(genre, {}).get(cat, 0) or 0
    gcomp = (gcw.get(genre, {}).get(cat, {}) or {}).get(comp, 0) or 0
    return float(cw) * float(gcomp)


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def main():
    books, gw, gcw = db_loader.load_from_db()
    orig = {r["Book"]: r for _, r in books.iterrows()}

    # engine estimation error + effective weight (from committed honest folds)
    est_abs = {c: [] for c in COMPS}
    eff_w = {c: [] for c in COMPS}
    for line in open(FOLDS, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        f = json.loads(line)
        if f.get("skip"):
            continue
        genre = f["genre"]
        actual = f["actual_components"]
        pred = f["variants"]["honest"]["components"]
        for c in COMPS:
            a, p = actual.get(c), pred.get(c)
            if a is None or p is None:
                continue
            if c in WB and a in (0, 0.0):
                continue
            est_abs[c].append(abs(p - a))
            eff_w[c].append(_eff_weight(c, genre, gw, gcw))

    # test-retest floor (blind re-rating vs original), same WB exclusion
    retest = db_write.get_retest_ratings()
    floor_abs = {c: [] for c in COMPS}
    for title, rr in retest.items():
        o = orig.get(title)
        if o is None:
            continue
        for c in COMPS:
            a, rv = o[c], rr.get(c)
            if a is None or rv is None:
                continue
            if c in WB and a in (0, 0.0):
                continue
            floor_abs[c].append(abs(float(a) - float(rv)))

    rows = []
    for c in COMPS:
        est = _mean(est_abs[c])
        floor = _mean(floor_abs[c])
        w = _mean(eff_w[c]) or 0.0
        headroom = (est - floor) if (est is not None and floor is not None) else None
        # reducible WA MAE if this component's estimation reached its floor
        red = (w * headroom) if headroom is not None else None
        rows.append({"c": c, "est": est, "floor": floor, "head": headroom,
                     "w": w, "red": red})

    rows.sort(key=lambda r: (r["red"] is None, -(r["red"] or 0)))

    L = ["# Component Headroom — noise floor (2.1) × estimation error (2.2)\n"]
    L.append(f"Retest floor from {len(retest)} blind re-ratings. **est** = engine honest "
             "estimation MAE; **floor** = test-retest MAE (irreducible); **headroom** = "
             "est − floor; **eff wt** = mean weight in WA; **reducible WA** = eff wt × "
             "headroom (WA MAE recoverable if this component hit its floor). Sorted by "
             "reducible WA.\n")
    L.append("| component | est MAE | floor | headroom | eff wt | reducible WA |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    tot_red = 0.0
    for r in rows:
        if r["red"]:
            tot_red += max(0.0, r["red"])
        name = r["c"] + (" *(WB)*" if r["c"] in WB else "")
        est = "-" if r["est"] is None else f"{r['est']:.3f}"
        floor = "-" if r["floor"] is None else f"{r['floor']:.3f}"
        head = "-" if r["head"] is None else f"{r['head']:+.3f}"
        red = "-" if r["red"] is None else f"{r['red']:+.4f}"
        L.append(f"| {name} | {est} | {floor} | {head} | {r['w']:.3f} | {red} |")
    L.append("")
    L.append(f"Sum of positive reducible WA ≈ **{tot_red:.3f}** — a rough ceiling on WA MAE "
             "recoverable by better component estimation alone (components already at/below "
             "their floor contribute nothing).\n")
    L.append("**Read:** components with high **reducible WA** are Branch-A targets; those with "
             "headroom ≈ 0 (or negative) are already as good as Michael's own re-rating "
             "consistency — intrinsically noisy, not worth chasing.\n")
    md = "\n".join(L) + "\n"
    open(OUT, "w", encoding="utf-8").write(md)
    print(md)
    print(f"  wrote {OUT}")


if __name__ == "__main__":
    main()
