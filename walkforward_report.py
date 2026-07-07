"""
walkforward_report.py
=====================
Summary report + delta_log reconciliation for the walk-forward backtest
(walkforward.py). Reads the deterministic folds artifact and emits a markdown
report + the rolling-MAE JSON the future track-record page will consume.

It computes only; it never predicts, never touches books.db except a read-only
delta_log query for the reconciliation section, and adds no API surface.

Sections (brief Task 2 + Task 3):
  * Overall WA MAE per variant (+ naive baseline)
  * WA MAE by genre and by year-read
  * Rolling WA MAE (trailing window) -- "engine getting smarter as the library
    grew"; underlying numbers persisted to walkforward_rolling_mae.json
  * Component-level MAE, worst-first (WB sentinel rows excluded, per the engine)
  * Interval coverage vs the nominal 90% level, per variant
  * Top-10 WA misses (honest variant) with analog source -- feature-idea fuel
  * raw vs corrected deltas -- where the correction helps / hurts, by genre + component
  * Reconciliation vs delta_log's real historical predictions (informational)

The report body carries no wall-clock timestamp, so re-running at the same commit
regenerates it byte-for-byte (provenance -- engine hash, git head -- comes from
the meta artifact and is stable per commit).
"""

import json
import os
import sqlite3

import walkforward as wf

LIVE = wf.LIVE
WB = wf.WB
VARIANTS = wf.VARIANTS
ROLL_WINDOW = wf.ROLL_WINDOW
NOMINAL = wf.NOMINAL_COVERAGE
# The brief's "CURRENT-CORRECTIONS" variant == our leaky (today's config).
VARIANT_LABEL = {"raw": "raw (no correction)",
                 "honest": "honest (walk-forward)",
                 "leaky": "leaky (today's config)"}


# ---------------------------------------------------------------------------
# small stats helpers (stdlib only)
# ---------------------------------------------------------------------------
def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _fmt(x, p=3):
    return "  -  " if x is None else f"{x:.{p}f}"


def _wa_abs(fold, variant):
    return fold["variants"][variant]["wa_abs_error"]


def _wa_signed(fold, variant):
    return fold["variants"][variant]["wa_signed_error"]


# ---------------------------------------------------------------------------
# aggregations
# ---------------------------------------------------------------------------
def overall_mae(folds):
    out = {v: _mean([_wa_abs(f, v) for f in folds]) for v in VARIANTS}
    # naive baseline: predict every book at the pool-mean actual WA is not
    # available per fold cheaply; use the constant-mean baseline over actuals.
    actuals = [f["actual_wa"] for f in folds]
    mu = _mean(actuals)
    out["naive_meanWA"] = _mean([abs(a - mu) for a in actuals]) if mu is not None else None
    return out


def mae_by_key(folds, keyfn):
    groups = {}
    for f in folds:
        groups.setdefault(keyfn(f), []).append(f)
    rows = []
    for k, fs in groups.items():
        rows.append({
            "key": k, "n": len(fs),
            **{v: _mean([_wa_abs(f, v) for f in fs]) for v in VARIANTS},
        })
    return rows


def component_mae(folds):
    """Per-component MAE per variant. WB components exclude rows whose ACTUAL
    value is the 0.0 'no worldbuilding' sentinel (matching the engine's own
    training exclusion) -- otherwise realist books inject ~8-point false errors."""
    rows = []
    for c in LIVE:
        rec = {"component": c, "is_wb": c in WB}
        n_used = None
        for v in VARIANTS:
            errs = []
            for f in folds:
                if c in WB and (f["actual_components"].get(c) in (0, 0.0)):
                    continue
                e = f["variants"][v]["component_abs_error"].get(c)
                if e is not None:
                    errs.append(e)
            rec[v] = _mean(errs)
            n_used = len(errs)
        rec["n"] = n_used
        rows.append(rec)
    rows.sort(key=lambda r: (r["honest"] is None, -(r["honest"] or 0)))
    return rows


def interval_coverage(folds):
    out = {}
    for v in VARIANTS:
        hits = [1 for f in folds if f["variants"][v]["ci_inside"]]
        out[v] = {"coverage": len(hits) / len(folds) if folds else None,
                  "n": len(folds)}
    return out


def served_interval_coverage(folds, residuals_path=None):
    """Coverage the CALIBRATED served interval would achieve on the honest
    walk-forward errors: bucket each fold by its honest same-author analog count
    (exactly as the live serving path does), look up that bucket's conformal
    half-width from calibration/residuals.json, and check whether the honest WA
    error falls inside. This is the interval the app actually shows a reader —
    unlike the overconfident ±1.645·resid_sd band the point-engine emits."""
    import intervals
    residuals_path = residuals_path or os.path.join(
        os.path.dirname(wf.OUT_DIR), "calibration", "residuals.json")
    table = intervals.load_residuals(residuals_path)
    if not table:
        return None
    hits = tot = 0
    for f in folds:
        na = f["variants"]["honest"]["n_author"]
        if na is None:
            continue
        info = intervals.interval_for(table, na)
        if not info:
            continue
        tot += 1
        if abs(f["variants"]["honest"]["wa_signed_error"]) <= info["half_width"] + 1e-12:
            hits += 1
    return {"coverage": hits / tot if tot else None, "n": tot,
            "source": os.path.relpath(residuals_path, os.path.dirname(wf.OUT_DIR))}


def rolling_mae(folds):
    """Trailing-window WA MAE per variant, plus the per-fold abs errors — the
    raw series the track-record page will plot."""
    series = []
    for i, f in enumerate(folds):
        window = folds[max(0, i - ROLL_WINDOW + 1): i + 1]
        row = {"position": f["position"], "title": f["title"],
               "pool_size": f["pool_size"], "window_n": len(window)}
        for v in VARIANTS:
            row[f"{v}_wa_abs_error"] = _wa_abs(f, v)
            row[f"{v}_rolling_mae"] = _mean([_wa_abs(w, v) for w in window])
        series.append(row)
    return {"window": ROLL_WINDOW, "variants": list(VARIANTS), "series": series}


def top_misses(folds, n=10, variant="honest"):
    ranked = sorted(folds, key=lambda f: -(_wa_abs(f, variant) or 0))
    out = []
    for f in ranked[:n]:
        vv = f["variants"][variant]
        out.append({
            "position": f["position"], "title": f["title"], "genre": f["genre"],
            "pool_size": f["pool_size"], "actual": f["actual_wa"],
            "pred": vv["wa"], "signed_err": vv["wa_signed_error"],
            "analog_src": vv["analog_src"],
            "n_author": vv["n_author"], "n_genre": vv["n_genre"],
        })
    return out


# ---------------------------------------------------------------------------
# reconciliation vs delta_log (Task 3)
# ---------------------------------------------------------------------------
def reconciliation(folds, db_path=None):
    """Match each genuine pre-read delta_log prediction (real per-second
    timestamp, untagged -- i.e. not a workbook-backfill midnight row nor a
    synthetic retro-sweep LOO row) against the harness's fold prediction for the
    same book. Purely informational: the two engines differ by version/model
    over time, so differences are expected and interesting, not failures."""
    db_path = db_path or wf.db_loader.DB
    rows = []
    try:
        uri = "file:" + os.path.abspath(db_path) + "?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        rows = con.execute(
            "SELECT title, logged_at, pred_wa, act_wa, pred_model FROM delta_log "
            "WHERE tag IS NULL AND logged_at NOT LIKE '%T00:00:00Z' "
            "ORDER BY logged_at").fetchall()
        con.close()
    except Exception:
        rows = []
    by_title = {f["title"]: f for f in folds}
    out = []
    for title, logged_at, pred_wa, act_wa, pred_model in rows:
        f = by_title.get(str(title).strip())
        out.append({
            "title": str(title).strip(), "logged_at": logged_at,
            "historical_pred": pred_wa, "historical_model": pred_model,
            "actual": act_wa,
            "harness_honest": f["variants"]["honest"]["wa"] if f else None,
            "harness_leaky": f["variants"]["leaky"]["wa"] if f else None,
            "harness_position": f["position"] if f else None,
        })
    return out


# ---------------------------------------------------------------------------
# markdown rendering
# ---------------------------------------------------------------------------
def _table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def render_markdown(folds, skips, meta):
    L = []
    ov = overall_mae(folds)
    L.append("# Walk-Forward Backtest — Report\n")
    L.append(f"Engine `{meta.get('engine_hash')}` · git `{(meta.get('git_head') or '')[:12]}` · "
             f"{meta.get('n_folds_evaluated')} folds over {meta.get('n_books_total')} books "
             f"(burn-in {meta.get('burn_in')}) · skipped {meta.get('skip_reasons')}.\n")
    L.append("Variants: **raw** = grounded research → WA, no correction · "
             "**honest** = author+genre correction fit on the *past-only pool* "
             "(the walk-forward baseline) · **leaky** = correction fit on the "
             "*full library* (today's config; saw future books).\n")

    # 1. Overall
    L.append("## Overall WA MAE\n")
    L.append(_table(
        ["variant", "WA MAE"],
        [[VARIANT_LABEL[v], _fmt(ov[v])] for v in VARIANTS]
        + [["_naive (predict mean WA)_", _fmt(ov["naive_meanWA"])]]))
    L.append("")

    # 2. By genre
    L.append("## WA MAE by genre  (raw → honest → leaky; Δ = honest−raw)\n")
    grows = sorted(mae_by_key(folds, lambda f: f["genre"]),
                   key=lambda r: (r["honest"] is None, r["honest"] or 0))
    L.append(_table(
        ["genre", "n", "raw", "honest", "leaky", "Δ honest−raw"],
        [[r["key"], r["n"], _fmt(r["raw"]), _fmt(r["honest"]), _fmt(r["leaky"]),
          _fmt((r["honest"] - r["raw"]) if r["honest"] is not None and r["raw"] is not None else None)]
         for r in grows]))
    L.append("")

    # 3. By year read
    L.append("## WA MAE by year read\n")
    yrows = sorted(mae_by_key(folds, lambda f: f["year_read"]),
                   key=lambda r: (r["key"] is None, r["key"]))
    L.append(_table(
        ["year", "n", "raw", "honest", "leaky"],
        [[r["key"], r["n"], _fmt(r["raw"]), _fmt(r["honest"]), _fmt(r["leaky"])]
         for r in yrows]))
    L.append("")

    # 4. Rolling MAE (summary; full series in the JSON sidecar)
    roll = rolling_mae(folds)
    L.append(f"## Rolling WA MAE  (trailing window = {ROLL_WINDOW} folds)\n")
    L.append("Full per-fold series in `walkforward_rolling_mae.json`. Endpoints:\n")
    if roll["series"]:
        a, b = roll["series"][0], roll["series"][-1]
        L.append(_table(
            ["", "position", "honest rolling", "leaky rolling", "raw rolling"],
            [["first", a["position"], _fmt(a["honest_rolling_mae"]), _fmt(a["leaky_rolling_mae"]), _fmt(a["raw_rolling_mae"])],
             ["last", b["position"], _fmt(b["honest_rolling_mae"]), _fmt(b["leaky_rolling_mae"]), _fmt(b["raw_rolling_mae"])]]))
    L.append("")

    # 5. Component MAE
    L.append("## Component MAE — worst first  (WB rows with actual=0 sentinel excluded)\n")
    crows = component_mae(folds)
    L.append(_table(
        ["component", "n", "raw", "honest", "leaky", "Δ honest−raw"],
        [[r["component"] + (" *(WB)*" if r["is_wb"] else ""), r["n"],
          _fmt(r["raw"]), _fmt(r["honest"]), _fmt(r["leaky"]),
          _fmt((r["honest"] - r["raw"]) if r["honest"] is not None and r["raw"] is not None else None)]
         for r in crows]))
    L.append("")

    # 6. Interval coverage
    L.append(f"## Interval coverage  (nominal {NOMINAL:.0%})\n")
    cov = interval_coverage(folds)
    L.append(_table(
        ["variant", "coverage", "n", "vs nominal"],
        [[VARIANT_LABEL[v], f"{cov[v]['coverage']:.1%}" if cov[v]['coverage'] is not None else "-",
          cov[v]["n"],
          f"{cov[v]['coverage'] - NOMINAL:+.1%}" if cov[v]['coverage'] is not None else "-"]
         for v in VARIANTS]))
    L.append("\n**Caveat — this is the point-engine's `±1.645·resid_sd` band, and it is "
             "overconfident by design.** `resid_sd`≈0.13 is the residual of the near-perfect "
             "WA-from-category-averages regression (WA is essentially a deterministic roll-up of "
             "the category averages), so the band is only ±0.21 WA — not a real prediction "
             "interval for researched components. The **calibrated** interval the app actually "
             "serves is the density-bucketed conformal table in `calibration/residuals.json`:\n")
    served = served_interval_coverage(folds)
    if served and served["coverage"] is not None:
        L.append(_table(
            ["served conformal interval (bucketed by author analogs)", "coverage", "n", "vs nominal"],
            [[f"honest errors vs `{served['source']}`", f"{served['coverage']:.1%}",
              served["n"], f"{served['coverage'] - NOMINAL:+.1%}"]]))
        L.append("\n_(The served table is sized on autonomous-engine LOO residuals; applying it to "
                 "researched errors is the faithful 'what interval does a reader see at this density' "
                 "check. Its ~80% target is the honest calibration story; the resid_sd band is not.)_")
    else:
        L.append("_(calibration/residuals.json not found — served-interval coverage skipped.)_")
    L.append("")

    # 7. Top misses
    L.append("## Top 10 WA misses — honest variant\n")
    L.append(_table(
        ["pos", "title", "genre", "pool", "actual", "pred", "signed err", "analog", "nA/nG"],
        [[m["position"], m["title"][:34], m["genre"][:20], m["pool_size"],
          _fmt(m["actual"], 2), _fmt(m["pred"], 2), _fmt(m["signed_err"], 2),
          m["analog_src"], f'{m["n_author"]}/{m["n_genre"]}']
         for m in top_misses(folds)]))
    L.append("")

    # 8. Where corrections help/hurt (summary read from the genre + component tables)
    help_g = [r for r in grows if r["honest"] is not None and r["raw"] is not None and r["honest"] < r["raw"]]
    hurt_g = [r for r in grows if r["honest"] is not None and r["raw"] is not None and r["honest"] > r["raw"]]
    L.append("## Raw → corrected: where the correction helps / hurts\n")
    L.append(f"- Genres where the walk-forward correction **beats raw**: {len(help_g)} "
             f"(best: {', '.join(r['key'] for r in sorted(help_g, key=lambda r: (r['honest']-r['raw']))[:3])}).")
    L.append(f"- Genres where it **hurts vs raw**: {len(hurt_g)} "
             f"({', '.join(r['key'] for r in sorted(hurt_g, key=lambda r: -(r['honest']-r['raw']))[:3])}).")
    overall_help = (ov["raw"] - ov["honest"]) if ov["raw"] and ov["honest"] else None
    L.append(f"- Overall, honest correction changes WA MAE by **{_fmt(-(overall_help or 0))}** vs raw "
             f"(negative = correction helps).")
    L.append("")

    # Task 3 reconciliation
    L.append("## Reconciliation vs delta_log  (genuine pre-read predictions; informational)\n")
    rec = reconciliation(folds)
    if rec:
        L.append(_table(
            ["title", "logged", "historical pred", "harness honest", "harness leaky", "actual", "status"],
            [[r["title"][:30], (r["logged_at"] or "")[:10], _fmt(r["historical_pred"], 2),
              _fmt(r["harness_honest"], 2), _fmt(r["harness_leaky"], 2), _fmt(r["actual"], 2),
              (f"evaluated (pos {r['harness_position']})" if r["harness_position"]
               else "not in current library")]
             for r in rec]))
        L.append("\nDifferences reflect engine/model drift between when each book "
                 "was really predicted and today's cached-vector re-prediction — "
                 "expected, not a failure. Rows marked _not in current library_ were "
                 "predicted + rated historically but are absent from today's `books` "
                 "table (removed / recategorised), so the harness has no fold for them.")
    else:
        L.append("_No genuine pre-read delta_log predictions found._")
    L.append("")

    return "\n".join(L) + "\n", roll, rec


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def build_report(out_dir):
    folds, skips = wf.load_folds(out_dir)
    meta_path = os.path.join(out_dir, wf.META_FILE)
    meta = {}
    if os.path.exists(meta_path):
        with open(meta_path) as fh:
            meta = json.load(fh)

    md, roll, _rec = render_markdown(folds, skips, meta)

    with open(os.path.join(out_dir, wf.REPORT_FILE), "w") as fh:
        fh.write(md)
    with open(os.path.join(out_dir, wf.ROLLING_FILE), "w") as fh:
        json.dump(roll, fh, indent=2, sort_keys=True)
        fh.write("\n")

    print(md)
    print(f"  wrote {os.path.join(out_dir, wf.REPORT_FILE)}")
    print(f"  wrote {os.path.join(out_dir, wf.ROLLING_FILE)}")


if __name__ == "__main__":
    build_report(wf.OUT_DIR)
