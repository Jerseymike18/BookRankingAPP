"""
retro_recs_report.py  (READ-ONLY)
=================================
Phase 3 deliverable: old-vs-new WA for every repredicted recommendation, the
distribution of the shift, the biggest movers with their component drivers, and
a per-genre summary. Compares the CURRENT recommendations table against the
preserved pre-reprediction snapshot. No writes, no API calls.

RUN:  python3 retro_recs_report.py [--snapshot calibration/recs_pre_reswept_2026-07-05.json]
"""
import argparse
import json
import sqlite3

import numpy as np

import db_loader
import db_write
import research_predict as rp

FC = db_write.FICTION_COMPONENTS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot",
                    default="calibration/recs_pre_reswept_2026-07-05.json")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    snap = {r["title"]: r for r in json.load(open(args.snapshot))["rows"]}
    _, gw, gcw = db_loader.load_from_db()
    con = sqlite3.connect(db_write.DB)
    con.row_factory = sqlite3.Row
    cur = {r["title"]: dict(r) for r in con.execute(
        'SELECT title, genre, ' + ", ".join(f'"{c}"' for c in FC)
        + ' FROM recommendations WHERE COALESCE(done,0)=0')}
    con.close()

    rows = []
    for t, s in snap.items():
        c = cur.get(t)
        if not c or s.get("old_wa") is None:
            continue
        new = {k: c[k] for k in FC}
        if any(v is None for v in new.values()):
            continue
        new_wa = rp._wa_from_components(new, c["genre"], gw, gcw)
        rows.append({"title": t, "genre": c["genre"], "old": s["old_wa"],
                     "new": new_wa, "d": new_wa - s["old_wa"],
                     "old_scores": s["old_scores"], "new_scores": new})

    n = len(rows)
    d = np.array([r["d"] for r in rows])
    print("=" * 76)
    print(f"PHASE 3 — RECOMMENDATIONS REPREDICTION REPORT  ({n} comparable rows)")
    print("=" * 76)
    print(f"  old WA: mean {np.mean([r['old'] for r in rows]):.2f}  "
          f"range [{min(r['old'] for r in rows):.2f}, {max(r['old'] for r in rows):.2f}]")
    print(f"  new WA: mean {np.mean([r['new'] for r in rows]):.2f}  "
          f"range [{min(r['new'] for r in rows):.2f}, {max(r['new'] for r in rows):.2f}]")
    print(f"  ΔWA   : mean {d.mean():+.3f}   mean|Δ| {np.abs(d).mean():.3f}   "
          f"sd {d.std():.3f}   max|Δ| {np.abs(d).max():.2f}")
    print(f"  direction: {(d<0).sum()} down, {(d>0).sum()} up, "
          f"{(np.abs(d)<0.1).sum()} ~unchanged (|Δ|<0.1)")
    print(f"  |Δ|>1.0: {(np.abs(d)>1.0).sum()}   |Δ|>0.5: {(np.abs(d)>0.5).sum()}")

    def drivers(r):
        cd = sorted(((c, (r["new_scores"][c] or 0) - (r["old_scores"].get(c) or 0))
                     for c in FC), key=lambda x: abs(x[1]), reverse=True)[:3]
        return ", ".join(f"{c} {v:+.1f}" for c, v in cd)

    for label, key in (("DROPPED most", lambda r: r["d"]),
                       ("ROSE most", lambda r: -r["d"])):
        srt = sorted(rows, key=key)[:args.top]
        print(f"\n  {label} (top {args.top}):")
        print(f"  {'title':<34}{'old':>6}{'new':>6}{'Δ':>7}  drivers")
        for r in srt:
            print(f"  {r['title'][:33]:<34}{r['old']:>6.2f}{r['new']:>6.2f}"
                  f"{r['d']:>+7.2f}  {drivers(r)}")

    print("\n  PER-GENRE mean ΔWA (n≥4):")
    by = {}
    for r in rows:
        by.setdefault(r["genre"], []).append(r["d"])
    for g, ds in sorted(by.items(), key=lambda kv: np.mean(kv[1])):
        if len(ds) >= 4:
            print(f"    {g:<28} n={len(ds):>3}  mean Δ {np.mean(ds):+.2f}")
    print("=" * 76)


if __name__ == "__main__":
    raise SystemExit(main())
