"""
retro_report.py  (READ-ONLY)
============================
PHASE 1 REPORT for the retro reprediction sweep. Reads the delta_log rows tagged
retro_sweep_v1_shrunk and reports, with NO writes and NO API calls:

  A. Per-component mean bias (act − raw pred), MAE, n  — the raw residuals that
     Phase 2 will refit the per-component constant corrections on.
  B. WA MAE: raw research prediction vs the pure-analog LOO baseline, and vs the
     EXISTING DeltaTracker per-component constants applied over a blend grid
     {0, 5, 10, 15, 20}% — "does the existing blend still help on Opus data?"
     Plus the in-sample-optimal constants (a Phase-2 upside preview, labelled
     optimistic).
  C. 80% conformal interval coverage: fraction of actual WA inside each book's
     bucketed interval (intervals.py + calibration/residuals.json), overall and
     per density bucket. Expected: mild overcoverage (research beats analog).

The raw prediction per component is pred_<c> in delta_log: the coded engine has
no DeltaTracker layer, so the logged prediction IS the pre-constant baseline and
d_<c> = act − pred is exactly the raw residual to calibrate on.

RUN:  python3 retro_report.py [--tag retro_sweep_v1_shrunk]
"""

import argparse
import json
import os
import sqlite3

import numpy as np

import db_loader
import db_write
import research_predict as rp        # _wa_from_components (reuse, don't reimplement)
import intervals

LIVE = db_write.FICTION_COMPONENTS   # canonical 14, delta_log order

# The EXISTING DeltaTracker per-component constants (workbook RatingGuidelines
# §6C, Sonnet-era n=4), in the act−pred convention (positive ⇒ add to pred).
# "Pacing" is a legacy 19-component-era name with no live-14 counterpart, so it
# is dropped and reported as unmapped rather than guessed onto another component.
EXISTING_CONSTANTS = {
    "Plot": -0.12, "Action": +0.14, "Depth": -0.075,
    "Motivations": -0.14, "Entertainment": +0.06,
}
UNMAPPED_LEGACY = {"Pacing": +0.10}
BLEND_GRID = [0.0, 0.05, 0.10, 0.15, 0.20]


def load_rows(tag):
    con = sqlite3.connect(db_write.DB)
    con.row_factory = sqlite3.Row
    pred_cols = [f'"pred_{db_write._col(c)}"' for c in LIVE]
    act_cols = [f'"act_{db_write._col(c)}"' for c in LIVE]
    d_cols = [f'"d_{db_write._col(c)}"' for c in LIVE]
    sql = ("SELECT title, pred_wa, act_wa, analog_wa, pred_genre, "
           "n_author, n_genre, " + ", ".join(pred_cols + act_cols + d_cols)
           + " FROM delta_log WHERE tag=? ORDER BY id")
    rows = [dict(r) for r in con.execute(sql, (tag,)).fetchall()]
    con.close()
    return rows


def _pred_vec(row):
    return {c: row[f"pred_{db_write._col(c)}"] for c in LIVE}


def _corrected_wa(row, gw, gcw, constants, w):
    """Roll up WA after adding w*constant to each mapped component (reusing the
    production WA rollup so it matches the engine exactly)."""
    scores = dict(_pred_vec(row))
    for c, k in constants.items():
        if scores.get(c) is not None:
            scores[c] = scores[c] + w * k
    return rp._wa_from_components(scores, row["pred_genre"], gw, gcw)


def _mae(pred, act):
    d = np.array(pred, float) - np.array(act, float)
    return float(np.mean(np.abs(d)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="retro_sweep_v1_shrunk")
    args = ap.parse_args()

    rows = load_rows(args.tag)
    _, gw, gcw = db_loader.load_from_db()
    n = len(rows)

    print("=" * 74)
    print(f"PHASE 1 REPORT — retro sweep '{args.tag}'   (n={n} books, READ-ONLY)")
    print("=" * 74)
    if n == 0:
        print("No rows logged under this tag yet — run retro_sweep.py first.")
        return 0

    act_wa = np.array([r["act_wa"] for r in rows], float)
    pred_wa = np.array([r["pred_wa"] for r in rows], float)
    analog_wa = np.array([r["analog_wa"] if r["analog_wa"] is not None else np.nan
                          for r in rows], float)

    # ---- A. Per-component bias / MAE / n --------------------------------------
    print("\nA. PER-COMPONENT RAW RESIDUALS  (bias = mean(act − raw pred))")
    print("-" * 74)
    print(f"  {'component':<24}{'n':>4}{'bias':>9}{'MAE':>9}{'|bias|>0.1?':>13}")
    print("  " + "-" * 62)
    comp_bias = {}
    for c in LIVE:
        d = np.array([r[f"d_{db_write._col(c)}"] for r in rows
                      if r[f"d_{db_write._col(c)}"] is not None], float)
        if d.size == 0:
            continue
        bias, mae = float(np.mean(d)), float(np.mean(np.abs(d)))
        comp_bias[c] = bias
        flag = "  <-- shift" if abs(bias) > 0.10 else ""
        print(f"  {c:<24}{d.size:>4}{bias:>+9.3f}{mae:>9.3f}{flag:>13}")

    # ---- B. WA MAE: raw vs analog vs existing-constant blend grid --------------
    raw_mae = _mae(pred_wa, act_wa)
    m = ~np.isnan(analog_wa)
    analog_mae = _mae(analog_wa[m], act_wa[m])
    print("\nB. WA MAE  (raw research prediction vs baselines)")
    print("-" * 74)
    print(f"  pure-analog LOO baseline WA MAE : {analog_mae:.4f}")
    print(f"  raw research prediction WA MAE  : {raw_mae:.4f}   "
          f"({'research beats analog' if raw_mae < analog_mae else 'analog beats research'} "
          f"by {abs(raw_mae-analog_mae):.4f})")
    print(f"  mean signed WA bias (act−pred)  : {float(np.mean(act_wa-pred_wa)):+.4f}")

    print("\n  EXISTING DeltaTracker constants over the blend grid "
          "(does the blend help?)")
    print(f"  mapped: {EXISTING_CONSTANTS}")
    print(f"  unmapped legacy (dropped): {UNMAPPED_LEGACY}")
    print(f"  {'blend w':>8}{'corrected WA MAE':>20}{'vs raw':>12}")
    print("  " + "-" * 40)
    for w in BLEND_GRID:
        cw = np.array([_corrected_wa(r, gw, gcw, EXISTING_CONSTANTS, w) for r in rows])
        mae = _mae(cw, act_wa)
        tag = "  (raw)" if w == 0 else (f"  {'HELPS' if mae < raw_mae else 'hurts'}")
        print(f"  {w:>8.2f}{mae:>20.4f}{mae-raw_mae:>+12.4f}{tag}")

    # In-sample-optimal constants (mean residual per mapped comp) — Phase-2 upside
    # preview; in-sample, so optimistic. Shown at full weight and at 10%.
    opt = {c: comp_bias.get(c, 0.0) for c in EXISTING_CONSTANTS}
    print("\n  In-sample-optimal constants (mean Opus residual per mapped comp) "
          "— OPTIMISTIC preview of Phase 2 (in-sample, not OOS):")
    print(f"    {{{', '.join(f'{c}:{v:+.3f}' for c, v in opt.items())}}}")
    for w in (0.10, 1.00):
        cw = np.array([_corrected_wa(r, gw, gcw, opt, w) for r in rows])
        print(f"    w={w:.2f}: corrected WA MAE {_mae(cw, act_wa):.4f} "
              f"(raw {raw_mae:.4f})")

    # ---- C. Interval coverage --------------------------------------------------
    print("\nC. 80% CONFORMAL INTERVAL COVERAGE  (bucket via n_author)")
    print("-" * 74)
    resid_path = os.path.join("calibration", "residuals.json")
    table = intervals.load_residuals(resid_path)
    if not table:
        print("  residuals.json not found — cannot score coverage.")
        return 0
    cur_hash = intervals.engine_hash()
    stale = table.get("engine_hash") != cur_hash
    print(f"  residuals engine_hash={table.get('engine_hash')} "
          f"{'(STALE vs live!)' if stale else '(matches live engine)'}")
    buckets = {}
    for r in rows:
        na = r["n_author"] if r["n_author"] is not None else 0
        info = intervals.interval_for(table, na, current_hash=cur_hash)
        if not info:
            continue
        hit = abs(r["act_wa"] - r["pred_wa"]) <= info["half_width"] + 1e-12
        b = info["bucket"]
        buckets.setdefault(b, [0, 0, info["half_width"]])
        buckets[b][0] += int(hit)
        buckets[b][1] += 1
    tot_hit = sum(v[0] for v in buckets.values())
    tot_n = sum(v[1] for v in buckets.values())
    print(f"  {'bucket':<20}{'half':>8}{'n':>5}{'covered':>10}   target 80%")
    print("  " + "-" * 52)
    for b in intervals.BUCKET_ORDER:
        if b in buckets:
            hits, cnt, hw = buckets[b]
            print(f"  {b:<20}{hw:>8.3f}{cnt:>5}{hits/cnt:>9.1%}")
    overall = tot_hit / tot_n if tot_n else float("nan")
    verdict = ("mild overcoverage (expected)" if overall > 0.80 else
               "undercoverage — investigate" if overall < 0.72 else "on target")
    print("  " + "-" * 52)
    print(f"  {'OVERALL':<20}{'':>8}{tot_n:>5}{overall:>9.1%}   → {verdict}")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
