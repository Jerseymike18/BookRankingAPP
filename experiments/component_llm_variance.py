#!/usr/bin/env python3
"""
component_llm_variance.py — how much of each component's estimation error is LLM
sampling noise vs irreducible? Compares two independent grounded passes
(llm_scores_richer.json vs web_grounded_cache.json) per component, alongside the
engine's estimation MAE (2.2) and the test-retest floor (2.1). High inter-pass
disagreement + high headroom ⇒ multi-sample averaging should help (that component's
error is largely LLM noise). Motivates the multisample_probe. Read-only.
"""
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_BACKEND", "sqlite")

import db_loader

LIVE = ["Plot", "Entertainment", "Action", "Ending", "Depth", "Emotional Impact",
        "Motivations", "Prose", "Narration", "Insights", "Thought-Provokingness",
        "Depth2", "Integration", "Originality"]
OUT = os.path.join(ROOT, "validation", "component_llm_variance.md")
# from validation/component_headroom.md (2.2 est-MAE, 2.1 floor)
EST = {"Plot": 0.844, "Entertainment": 0.830, "Action": 0.832, "Ending": 1.200,
       "Depth": 0.808, "Emotional Impact": 1.056, "Motivations": 0.834, "Prose": 0.657,
       "Narration": 0.869, "Insights": 0.709, "Thought-Provokingness": 0.812,
       "Depth2": 0.830, "Integration": 0.831, "Originality": 0.785}
FLOOR = {"Plot": 0.506, "Entertainment": 0.512, "Action": 0.554, "Ending": 0.665,
         "Depth": 0.485, "Emotional Impact": 0.988, "Motivations": 0.715, "Prose": 0.708,
         "Narration": 0.792, "Insights": 0.646, "Thought-Provokingness": 0.665,
         "Depth2": 0.752, "Integration": 0.862, "Originality": 0.648}


def main():
    rich = json.load(open(os.path.join(ROOT, "llm_scores_richer.json")))
    grnd = json.load(open(os.path.join(ROOT, "web_grounded_cache.json")))
    books, _, _ = db_loader.load_from_db()
    rated = set(books["Book"])
    common = [t for t in rated if t in rich and t in grnd
              and isinstance(rich[t].get("scores"), dict)
              and isinstance(grnd[t].get("scores"), dict)]

    rows = []
    for c in LIVE:
        ds = [abs(float(rich[t]["scores"][c]) - float(grnd[t]["scores"][c]))
              for t in common if c in rich[t]["scores"] and c in grnd[t]["scores"]]
        d = float(np.mean(ds)) if ds else None
        rows.append((c, d, EST[c], FLOOR[c], EST[c] - FLOOR[c]))
    rows.sort(key=lambda r: -(r[4]))     # by reducible headroom

    L = ["# Component LLM variance vs headroom (new-signal diagnostic)\n"]
    L.append(f"{len(common)}/{len(rated)} rated books have two independent grounded passes. "
             "**inter-pass |Δ|** = mean disagreement between the two passes (a proxy for LLM "
             "sampling noise); **est-MAE** = engine estimation error (2.2); **floor** = "
             "test-retest noise (2.1); **reducible** = est−floor.\n")
    L.append("| component | inter-pass \\|Δ\\| | est-MAE | floor | reducible |")
    L.append("| --- | --- | --- | --- | --- |")
    for c, d, e, f, red in rows:
        L.append(f"| {c} | {'-' if d is None else f'{d:.3f}'} | {e:.3f} | {f:.3f} | {red:+.3f} |")
    L.append("")
    L.append("**Read:** components high on BOTH inter-pass |Δ| and reducible headroom "
             "(Ending 0.62/+0.53, Depth 0.55/+0.32, Plot 0.44/+0.34) carry error that is "
             "largely LLM noise ⇒ multi-sample averaging targets them. Emotional Impact has "
             "high disagreement but ~0 headroom (floor-limited) — averaging can't help its WA.\n")
    open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))
    print(f"  wrote {OUT}")


if __name__ == "__main__":
    main()
