"""
retro_recalibrate.py
====================
PHASE 2 (gated) — recalibrate the per-component constant corrections (the
workbook "DeltaTracker" layer) on the Opus retro-sweep raw residuals, validated
STRICTLY out-of-sample within the sweep.

Functional form (unchanged): a CONSTANT per component k_c = mean raw residual
(act − raw pred), applied at a single global BLEND weight w:
    corrected_c = pred_c + w * k_c   →   corrected WA rolled up via the engine's
own WA formula (research_predict._wa_from_components). No feature-conditional
corrections; that is a separate future project.

Worldbuilding (Depth2/Integration/Originality): realist books store WB actual as
the 0.0 sentinel while the LLM scores it normally, so those rows are EXCLUDED
from the WB constants (matching reresearch_and_measure.correct_book). A constant
WB correction fit on the sentinel rows would be a −5-to−6 artifact that wrecks
real-worldbuilding books.

VALIDATION — leave-one-book-out within the sweep: for each held-out book the
constants are refit on the OTHER 126 rows and applied to it. GATE:
  (1) OOS corrected WA MAE < raw WA MAE, and
  (2) no component's OOS MAE worse than raw by > 0.05 — any that fails has its
      constant ZEROED rather than shipping a harmful correction.
The blend weight is tuned over {0,5,10,15,20}% on the OOS WA MAE.

Output: a report + calibration/recalibration_v2.json (the shippable constants +
blend + gate result) for the Phase 5.1 workbook sync. READ-ONLY w.r.t. the DB and
the engine; writes only the calibration artifact.

RUN:  python3 retro_recalibrate.py [--tag retro_sweep_v1_shrunk]
"""

import argparse
import json
import os
import sqlite3

import numpy as np

import db_loader
import db_write
import research_predict as rp        # _wa_from_components (engine WA rollup; reuse)

LIVE = db_write.FICTION_COMPONENTS
WB = ["Depth2", "Integration", "Originality"]
BLEND_GRID = [0.0, 0.05, 0.10, 0.15, 0.20]
WORSE_TOL = 0.05                     # per-component "materially worse" gate
# The Sonnet-era set the workbook currently corrects (for the report's context).
LEGACY_SET = ["Plot", "Pacing", "Action", "Depth", "Motivations", "Entertainment"]


def load_rows(tag):
    con = sqlite3.connect(db_write.DB)
    con.row_factory = sqlite3.Row
    cols = []
    for pfx in ("pred_", "act_", "d_"):
        cols += [f'"{pfx}{db_write._col(c)}"' for c in LIVE]
    sql = ("SELECT title, pred_genre, n_author, pred_wa, act_wa, "
           + ", ".join(cols) + " FROM delta_log WHERE tag=? ORDER BY id")
    rows = [dict(r) for r in con.execute(sql, (tag,)).fetchall()]
    con.close()
    for r in rows:
        r["_sentinel"] = all((r[f"act_{db_write._col(c)}"] or 0) == 0 for c in WB)
    return rows


def fit_constants(rows):
    """k_c = mean raw residual (d_c) over rows; WB comps exclude sentinel rows."""
    k = {}
    for c in LIVE:
        pool = [r for r in rows if not (c in WB and r["_sentinel"])]
        d = np.array([r[f"d_{db_write._col(c)}"] for r in pool], float)
        k[c] = float(d.mean()) if d.size else 0.0
    return k


def corrected_wa(row, k, w, gw, gcw):
    scores = {c: row[f"pred_{db_write._col(c)}"] + w * k.get(c, 0.0) for c in LIVE}
    return rp._wa_from_components(scores, row["pred_genre"], gw, gcw)


def loo_eval(rows, w, gw, gcw, mask=None):
    """Leave-one-book-out OOS. mask: optional set of components allowed a nonzero
    constant (others forced to 0). Returns (wa_mae, {comp: comp_mae})."""
    wa_abs, comp_abs = [], {c: [] for c in LIVE}
    for i, row in enumerate(rows):
        train = rows[:i] + rows[i + 1:]
        k = fit_constants(train)
        if mask is not None:
            k = {c: (k[c] if c in mask else 0.0) for c in LIVE}
        wa_abs.append(abs(row["act_wa"] - corrected_wa(row, k, w, gw, gcw)))
        for c in LIVE:
            kc = k.get(c, 0.0) if (mask is None or c in mask) else 0.0
            pred_c = row[f"pred_{db_write._col(c)}"] + w * kc
            comp_abs[c].append(abs(row[f"act_{db_write._col(c)}"] - pred_c))
    wa_mae = float(np.mean(wa_abs))
    comp_mae = {c: float(np.mean(v)) for c, v in comp_abs.items()}
    return wa_mae, comp_mae


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="retro_sweep_v1_shrunk")
    ap.add_argument("--out", default=os.path.join("calibration", "recalibration_v2.json"))
    args = ap.parse_args()

    rows = load_rows(args.tag)
    _, gw, gcw = db_loader.load_from_db()
    n = len(rows)
    print("=" * 76)
    print(f"PHASE 2 — correction recalibration  (tag={args.tag}, n={n}, LOO OOS)")
    print("=" * 76)

    # Baselines (w=0 is raw).
    raw_wa_mae, raw_comp_mae = loo_eval(rows, 0.0, gw, gcw)
    full_k = fit_constants(rows)      # full-data constants (the shippable magnitudes)

    # ---- Tune blend weight on OOS WA MAE, all components active ----------------
    print(f"\nBLEND-WEIGHT SWEEP (all 14 components active, OOS WA MAE)")
    print(f"  raw (w=0) WA MAE : {raw_wa_mae:.4f}")
    print(f"  {'w':>6}{'OOS WA MAE':>13}{'vs raw':>11}")
    grid = {}
    for w in BLEND_GRID:
        wa_mae, _ = loo_eval(rows, w, gw, gcw)
        grid[w] = wa_mae
        tag = "  ← best-so-far" if wa_mae == min(grid.values()) else ""
        print(f"  {w:>6.2f}{wa_mae:>13.4f}{wa_mae-raw_wa_mae:>+11.4f}{tag}")
    best_w = min(grid, key=grid.get)

    # ---- Per-component gate at best_w: zero any component made worse -----------
    _, comp_mae_bw = loo_eval(rows, best_w if best_w > 0 else 0.10, gw, gcw)
    gate_w = best_w if best_w > 0 else 0.10   # evaluate component harm at a real weight
    survivors = []
    print(f"\nPER-COMPONENT GATE (OOS MAE, correction @ w={gate_w:.2f} vs raw; "
          f"zero if worse by > {WORSE_TOL})")
    print(f"  {'component':<22}{'k_c':>8}{'raw MAE':>10}{'corr MAE':>10}{'Δ':>8}  verdict")
    for c in LIVE:
        raw_m, cor_m = raw_comp_mae[c], comp_mae_bw[c]
        worse = cor_m - raw_m
        keep = worse <= WORSE_TOL and abs(full_k[c]) > 1e-9
        if keep:
            survivors.append(c)
        note = "keep" if keep else ("zero (harmful)" if worse > WORSE_TOL else "zero (~0)")
        legacy = " *legacy" if c in LEGACY_SET else ""
        print(f"  {c:<22}{full_k[c]:>+8.3f}{raw_m:>10.3f}{cor_m:>10.3f}{worse:>+8.3f}  "
              f"{note}{legacy}")

    # ---- Re-tune blend weight with ONLY surviving components active -----------
    print(f"\nBLEND-WEIGHT SWEEP (only {len(survivors)} surviving components, OOS WA MAE)")
    mask = set(survivors)
    grid2 = {}
    for w in BLEND_GRID:
        wa_mae, _ = loo_eval(rows, w, gw, gcw, mask=mask)
        grid2[w] = wa_mae
        print(f"  {w:>6.2f}{wa_mae:>13.4f}{wa_mae-raw_wa_mae:>+11.4f}")
    best_w2 = min(grid2, key=grid2.get)
    best_mae2 = grid2[best_w2]

    # ---- Final gate -----------------------------------------------------------
    overall_pass = best_mae2 < raw_wa_mae - 1e-9 and best_w2 > 0
    print("\n" + "=" * 76)
    print("GATE DECISION")
    print("=" * 76)
    print(f"  raw OOS WA MAE            : {raw_wa_mae:.4f}")
    print(f"  best corrected OOS WA MAE : {best_mae2:.4f}  (w={best_w2:.2f}, "
          f"{len(survivors)} comps)")
    print(f"  overall gate (corrected < raw OOS): "
          f"{'PASS' if overall_pass else 'FAIL'}")

    if overall_pass:
        ship_constants = {c: round(full_k[c], 4) for c in survivors}
        ship_blend = best_w2
        decision = "apply"
        print(f"  → SHIP constants for {survivors} at blend {ship_blend:.2f}")
    else:
        ship_constants = {c: 0.0 for c in LIVE}
        ship_blend = 0.0
        decision = "retire"
        print("  → No blend weight beats raw out-of-sample. RETIRE the per-component")
        print("    constant layer for the Opus engine (ship zeros): the Opus researcher")
        print("    + author_genre correction already centres per-component bias.")
    print("=" * 76)

    artifact = {
        "tag": args.tag, "n_books": n, "method": "constant-per-component, blended",
        "validation": "leave-one-book-out within sweep",
        "raw_wa_mae": round(raw_wa_mae, 4),
        "best_corrected_wa_mae": round(best_mae2, 4),
        "blend_grid": {f"{w:.2f}": round(grid2[w], 4) for w in BLEND_GRID},
        "full_data_constants": {c: round(full_k[c], 4) for c in LIVE},
        "survivors": survivors,
        "gate_pass": bool(overall_pass),
        "decision": decision,
        "ship_constants": ship_constants,
        "ship_blend_weight": ship_blend,
        "legacy_set": LEGACY_SET,
        "note": ("Recomputed against Opus biases (CLAUDE.md DeltaTracker note). "
                 "WB constants exclude realist-sentinel rows. Workbook-only (scope A); "
                 "the coded engine is unchanged."),
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(artifact, f, indent=2)
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
