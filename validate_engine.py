"""
validate_engine.py
==================
PHASE 2: Honest leave-one-out (LOO) validation of the full prediction engine,
plus the acceptance gate for the empirical-Bayes analog-shrinkage change.

THE QUESTION THIS ANSWERS
-------------------------
"How accurate is the engine on a book it has NEVER seen?" -- and, more usefully,
"which parts of it are real signal versus the model just recognising books it
already memorised?"

HOW IT STAYS HONEST
-------------------
For each book we:
  1. REMOVE it from the dataset.
  2. Refit the regression + per-genre biases on the OTHER books only.
  3. Predict the removed book from scratch (autonomous component estimate ->
     category averages -> regression -> bias -> blend), exactly as the engine
     would for a brand-new book.
  4. Compare the prediction to the book's real WA.
This "refit without the test book" step is what makes the number trustworthy.

WHAT IT REPORTS
---------------
  A. Headline WA accuracy of the autonomous engine vs. the naive baseline.
  B. Per-genre WA accuracy.
  C. Per-COMPONENT accuracy -- which of the components carry real signal.
  D. Analog-mode A/B: the SAME LOO sweep run under both the "hard" fallback and
     the "shrunk" empirical-Bayes estimator, compared on overall WA MAE, overall
     component MAE, per-genre MAE, and MAE bucketed by data density. The density
     buckets are where the shrinkage is expected to help (thin author pools) or
     to risk harm (data-rich pools). This section also evaluates the acceptance
     gate that governs whether predict_engine.ANALOG_MODE may flip to "shrunk".

DENSITY BUCKETS (see _density_bucket)
-------------------------------------
predict_engine has three analog tiers: global -> genre -> author. `author` is
the innermost/tightest pool (there is no sub-author "cluster" grouping), so the
brief's four density strata map onto author-peer counts:
  cluster n>=6 .... >=6 same-author training books (data-rich; must not regress)
  cluster 2<=n<6 .. 2-5 same-author training books (small-n; the expected win)
  author-only n=1 . exactly 1 peer (hard mode discards it and falls to genre)
  genre-only n=0 .. no author peers (genre / global prior only)
Peer counts are per fold (n-1 for an author with n books total).

HOW TO RUN: python3 validate_engine.py   (slow: it refits once per book, then
sweeps the k grid and runs both modes over the cached folds).
Needs predict_engine.py + db_loader.py in the same folder.
"""

import json
import os

import numpy as np
import pandas as pd

# Reuse the engine we already built & validated.
import predict_engine as pe
# Canonical density-bucket definition + conformal-interval helpers. Importing it
# here (rather than redefining the buckets) guarantees the LOO residual table and
# the live serving path bucket predictions identically — no drift, no miscoverage.
import intervals

WORKBOOK = pe.WORKBOOK

# k grid for the shrinkage tuning sweep (shared per tier; see tune_k).
DEFAULT_K_GRID = [0.5, 1, 2, 3, 5, 8, 12]

# Display order for the density buckets (single source of truth: intervals.py).
BUCKET_ORDER = intervals.BUCKET_ORDER


# ---------------------------------------------------------------------------
# Helpers to refit the engine on an arbitrary subset of books
# ---------------------------------------------------------------------------
def fit_on(train_books):
    """Refit regression + biases + upstream on a given training set."""
    coeffs, r2, resid_sd = pe.fit_regression(train_books)
    ginfo = pe.genre_bias_and_trust(train_books, coeffs)
    upstream = pe.fit_upstream(train_books)
    return coeffs, r2, resid_sd, ginfo, upstream


def predict_one(test_row, train_books, gw, gcw, coeffs, resid_sd, ginfo,
                upstream, apply_bias=True, mode=None, k_author=None,
                k_genre=None):
    """
    Predict a single held-out book from scratch using only the training set.
    `mode`/`k_author`/`k_genre` select the per-component analog estimator (see
    predict_engine.estimate_components); mode=None uses the module default.
    The WA-level model/analog blend below is deliberately UNCHANGED by mode --
    the shrinkage change is scoped to the per-component baseline only.
    Returns (predicted_WA, estimated_components_dict).
    """
    author, genre = test_row["Author"], test_row["Genre"]
    est, _, _ = pe.estimate_components(train_books, author, genre, upstream,
                                       mode=mode, k_author=k_author,
                                       k_genre=k_genre)
    wcats = pe.components_to_wcats(est, genre, gcw)
    wa_model = pe.regression_wa(coeffs, wcats["Story"], wcats["Character"],
                                wcats["Aesthetics"], wcats["Theme"])
    g = ginfo.get(genre, {"bias": 0.0, "n": 0, "trust": 0.0})
    wa_corrected = wa_model + (g["bias"] if apply_bias else 0.0)

    analog = train_books[train_books["Author"] == author]["WA"].dropna()
    if len(analog) < 2:
        analog = train_books[train_books["Genre"] == genre]["WA"].dropna()
    analog_mean = float(analog.mean()) if len(analog) else wa_corrected
    trust = g["trust"]
    wa_final = trust * wa_corrected + (1 - trust) * analog_mean
    return wa_final, est


# ---------------------------------------------------------------------------
# LOO core — build folds once, then sweep estimator modes / k over them
# ---------------------------------------------------------------------------
def _build_folds(books):
    """Refit the engine once per leave-one-out fold and cache the pieces that do
    NOT depend on the shrinkage constants (regression, per-genre biases, upstream
    model). The k grid-search and the hard/shrunk A/B reuse these cached folds so
    the ~127 expensive refits happen only once."""
    folds = []
    for i in books.index.tolist():
        test_row = books.loc[i]
        train = books.drop(i)
        coeffs, r2, resid_sd, ginfo, upstream = fit_on(train)
        n_peers = int((train["Author"] == test_row["Author"]).sum())
        n_genre = int((train["Genre"] == test_row["Genre"]).sum())
        folds.append({"i": i, "test_row": test_row, "train": train,
                      "coeffs": coeffs, "resid_sd": resid_sd, "ginfo": ginfo,
                      "upstream": upstream, "n_peers": n_peers,
                      "n_genre": n_genre})
    return folds


def _loo_sweep(folds, books, gw, gcw, mode, k_author=None, k_genre=None,
               apply_bias=True):
    """Predict every held-out book under one estimator config. Returns
    (wa_pred aligned to books row order, comp_err dict, n_peers list)."""
    comps = pe.components_of(books)
    wa_pred, n_peers = [], []
    comp_err = {c: [] for c in comps}
    for f in folds:
        wa, est = predict_one(f["test_row"], f["train"], gw, gcw, f["coeffs"],
                              f["resid_sd"], f["ginfo"], f["upstream"],
                              apply_bias=apply_bias, mode=mode,
                              k_author=k_author, k_genre=k_genre)
        wa_pred.append(wa)
        n_peers.append(f["n_peers"])
        tr = f["test_row"]
        for c in comps:
            a = tr[c]
            if a is not None and not (isinstance(a, float) and np.isnan(a)):
                comp_err[c].append(abs(est[c] - a))
    return np.array(wa_pred), comp_err, n_peers


def _summarize(books, wa_pred, comp_err, n_peers):
    """Build the metrics dict for one LOO sweep: overall WA + component MAE,
    per-genre MAE, per-component MAE, and WA MAE per density bucket."""
    actual = books["WA"].values
    m = ~np.isnan(wa_pred)
    abs_err = np.abs(wa_pred[m] - actual[m])
    naive = float(np.abs(actual - actual.mean()).mean())
    overall_wa_mae = float(abs_err.mean())

    all_comp = [e for errs in comp_err.values() for e in errs]
    overall_comp_mae = float(np.mean(all_comp)) if all_comp else float("nan")

    per_component = []
    for c, errs in comp_err.items():
        if errs:
            mm = float(np.mean(errs))
            verdict = ("strong signal" if mm < 0.9 else
                       "moderate" if mm < 1.15 else "weak / noisy")
            per_component.append({"component": c, "mae": round(mm, 4),
                                  "n": len(errs), "verdict": verdict})
    per_component.sort(key=lambda x: x["mae"])

    tmp = books.copy()
    tmp["pred"] = wa_pred
    per_genre = []
    for g, sub in tmp.groupby("Genre"):
        e = float(np.abs(sub["pred"] - sub["WA"]).mean())
        ng = len(sub)
        verdict = ("thin" if ng < 5 else
                   "strong" if e < 0.6 else "okay" if e < 0.9 else "weak")
        per_genre.append({"genre": g, "n": ng, "mae": round(e, 4),
                          "verdict": verdict})
    per_genre.sort(key=lambda x: x["mae"])

    npa = np.array([intervals.density_bucket(int(x)) for x in n_peers])
    by_bucket = {}
    for b in BUCKET_ORDER:
        mask = (npa == b) & m
        if mask.sum():
            be = np.abs(wa_pred[mask] - actual[mask])
            by_bucket[b] = {"mae": round(float(be.mean()), 4),
                            "n": int(mask.sum())}
        else:
            by_bucket[b] = {"mae": None, "n": 0}

    return {
        "naive_mae": round(naive, 4),
        "engine_mae": round(overall_wa_mae, 4),      # legacy alias
        "overall_wa_mae": round(overall_wa_mae, 4),
        "overall_comp_mae": round(overall_comp_mae, 4),
        "within_0_5": round(float((abs_err <= 0.5).mean()), 4),
        "within_1_0": round(float((abs_err <= 1.0).mean()), 4),
        "improvement_pct": round((naive - overall_wa_mae) / naive * 100, 1)
        if naive else 0.0,
        "per_genre": per_genre,
        "per_component": per_component,
        "by_bucket": by_bucket,
    }


# ---------------------------------------------------------------------------
# Public LOO entry point (backward compatible: run_loo(books=, gw=, gcw=))
# ---------------------------------------------------------------------------
def run_loo(books=None, gw=None, gcw=None, mode=None, k_author=None,
            k_genre=None, folds=None):
    """
    Run leave-one-out validation for ONE estimator config and return structured
    results. mode=None uses predict_engine.ANALOG_MODE (the live engine).
    Keeps the historical return keys (per_component, per_genre, engine_mae,
    naive_mae, within_*, bias_*, improvement_pct) so existing callers such as
    compare_researchers.py keep working, and adds overall_wa_mae /
    overall_comp_mae / by_bucket / mode.
    """
    if books is None:
        import db_loader
        books, gw, gcw = db_loader.load_from_db()
    if folds is None:
        folds = _build_folds(books)

    wa_bias, comp_err, n_peers = _loo_sweep(folds, books, gw, gcw, mode,
                                            k_author, k_genre, apply_bias=True)
    wa_nobias, _, _ = _loo_sweep(folds, books, gw, gcw, mode, k_author,
                                 k_genre, apply_bias=False)

    s = _summarize(books, wa_bias, comp_err, n_peers)
    actual = books["WA"].values

    def _mae(p):
        mm = ~np.isnan(p)
        return float(np.abs(p[mm] - actual[mm]).mean())

    mb, mnb = _mae(wa_bias), _mae(wa_nobias)
    s.update({
        "n_books": len(books),
        "bias_mae": round(mb, 4),
        "no_bias_mae": round(mnb, 4),
        "bias_helps": bool(mb < mnb),
        "bias_delta": round(mnb - mb, 4),
        "mode": mode if mode is not None else pe.ANALOG_MODE,
    })
    return s


# ---------------------------------------------------------------------------
# Conformal residual table (offline snapshot that powers prediction intervals)
# ---------------------------------------------------------------------------
# The serving path (backend/main.py) must NEVER run LOO per request (~127
# refits). Instead this persists, ONCE and offline, a small residuals.json that
# maps each density bucket to an empirical interval half-width; intervals.py
# reads it at serve time. Regenerate after any engine-math change:
#     python3 validate_engine.py --write-residuals
#
# The residual is (actual - predicted) under the SHRUNK estimator with the
# genre-bias correction applied -- i.e. the error of the exact number the user
# is shown -- so the interval is honest about the served prediction.

def half_width_from_residuals(residuals, target=intervals.COVERAGE_TARGET):
    """Symmetric interval half-width = the target-th percentile of |residual|.
    Pure and unit-tested. Empty input -> None."""
    r = np.abs(np.asarray(residuals, dtype=float))
    if r.size == 0:
        return None
    return float(np.percentile(r, target * 100.0))


def coverage(residuals, half_width):
    """Fraction of residuals whose magnitude is within half_width -- i.e. the
    actual WA lands inside [pred-hw, pred+hw]. Pure and unit-tested."""
    r = np.abs(np.asarray(residuals, dtype=float))
    if r.size == 0 or half_width is None:
        return None
    return float((r <= half_width + 1e-12).mean())


def _pooled_residuals(bucket, by_bucket, all_residuals):
    """The residual set used to SIZE `bucket`'s interval: its own residuals, or
    -- when it is thin (< MIN_BUCKET_N) -- pooled with its nearest neighbour by
    data richness (see intervals.POOL_PARTNER). Returns (residuals, pooled)."""
    own = list(by_bucket.get(bucket, []))
    if not intervals.should_pool(bucket, len(own)):
        return own, False
    partner = intervals.POOL_PARTNER[bucket]
    if partner == "__global__":
        # Pool with the WHOLE set (which already contains `own`).
        return list(all_residuals), True
    # A named partner is a disjoint bucket, so concatenate.
    return own + list(by_bucket.get(partner, [])), True


def build_residual_table(books=None, gw=None, gcw=None, folds=None):
    """Run the LOO sweep under the SHRUNK estimator (the live engine) and return
    the residual-table dict: per-book residuals + density stats, and per-bucket
    half-widths / signed quantiles / mean residual / pooling flags, plus
    in-sample coverage diagnostics. Pure w.r.t. disk (see write_residuals)."""
    import datetime
    if books is None:
        import db_loader
        books, gw, gcw = db_loader.load_from_db()
    if folds is None:
        folds = _build_folds(books)

    wa_pred, _, _ = _loo_sweep(folds, books, gw, gcw, "shrunk", apply_bias=True)
    actual = books["WA"].values
    titles = books["Book"].tolist()

    records, by_bucket = [], {b: [] for b in BUCKET_ORDER}
    for k, f in enumerate(folds):
        na, ng = int(f["n_peers"]), int(f["n_genre"])
        bucket = intervals.density_bucket(na)
        resid = float(actual[k] - wa_pred[k])
        records.append({"book": titles[k], "actual": round(float(actual[k]), 4),
                        "pred": round(float(wa_pred[k]), 4),
                        "residual": round(resid, 4),
                        "n_author": na, "n_genre": ng, "bucket": bucket})
        by_bucket[bucket].append(resid)

    all_resid = [r["residual"] for r in records]
    buckets_out = {}
    for b in BUCKET_ORDER:
        own = by_bucket[b]
        resid_set, pooled = _pooled_residuals(b, by_bucket, all_resid)
        hw = half_width_from_residuals(resid_set)
        buckets_out[b] = {
            "n": len(own),
            "half_width": None if hw is None else round(hw, 4),
            "q10": None if not own else round(float(np.percentile(own, 10)), 4),
            "q90": None if not own else round(float(np.percentile(own, 90)), 4),
            "mean_residual": None if not own else round(float(np.mean(own)), 4),
            "pooled": pooled,
            "pooled_with": intervals.POOL_PARTNER[b] if pooled else None,
            "pool_n": len(resid_set),
        }

    # In-sample coverage using each book's FINAL (possibly pooled) half-width --
    # the same half-width the serving path would apply to that density.
    cov_hits, cov_tot = 0, 0
    by_bucket_cov = {b: [0, 0] for b in BUCKET_ORDER}
    for r in records:
        hw = buckets_out[r["bucket"]]["half_width"]
        if hw is None:
            continue
        hit = abs(r["residual"]) <= hw + 1e-12
        cov_hits += hit
        cov_tot += 1
        by_bucket_cov[r["bucket"]][0] += hit
        by_bucket_cov[r["bucket"]][1] += 1

    coverage_out = {
        "target": intervals.COVERAGE_TARGET,
        "overall": None if not cov_tot else round(cov_hits / cov_tot, 4),
        "by_bucket": {b: (None if c[1] == 0 else round(c[0] / c[1], 4))
                      for b, c in by_bucket_cov.items()},
        "by_bucket_n": {b: c[1] for b, c in by_bucket_cov.items()},
    }

    return {
        "generated_at":
            datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "engine_hash": intervals.engine_hash(),
        "analog_mode": "shrunk",
        "k_author": pe.K_AUTHOR,
        "k_genre": pe.K_GENRE,
        "n_books": len(records),
        "coverage_target": intervals.COVERAGE_TARGET,
        "min_bucket_n": intervals.MIN_BUCKET_N,
        "buckets": buckets_out,
        "coverage": coverage_out,
        "residuals": records,
    }


def write_residuals(out_path=None, books=None, gw=None, gcw=None, folds=None):
    """Build the residual table and write it atomically to `out_path`
    (default calibration/residuals.json). Returns the table dict."""
    out_path = out_path or os.path.join("calibration", "residuals.json")
    table = build_residual_table(books, gw, gcw, folds)
    out_dir = os.path.dirname(out_path) or "."
    os.makedirs(out_dir, exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(table, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, out_path)
    return table


# ---------------------------------------------------------------------------
# Shrinkage tuning + hard/shrunk A/B + acceptance gate
# ---------------------------------------------------------------------------
def tune_k(folds, books, gw, gcw, grid=None):
    """Grid-search k_author x k_genre minimising overall LOO component MAE under
    the shrunk estimator. Returns a list of {k_author, k_genre, comp_mae, wa_mae}
    sorted best-first.

    LEAKAGE NOTE: k is tuned and evaluated on the SAME leave-one-book-out sweep
    (the cheap approximation the brief permits), so the winning MAE carries a
    known mild optimism. The caller reports the second-best cell as a
    sensitivity check.
    """
    grid = grid or DEFAULT_K_GRID
    actual = books["WA"].values
    results = []
    for ka in grid:
        for kg in grid:
            wa, comp_err, _ = _loo_sweep(folds, books, gw, gcw, "shrunk",
                                         ka, kg, apply_bias=True)
            allc = [e for errs in comp_err.values() for e in errs]
            m = ~np.isnan(wa)
            results.append({
                "k_author": ka, "k_genre": kg,
                "comp_mae": round(float(np.mean(allc)), 5),
                "wa_mae": round(float(np.abs(wa[m] - actual[m]).mean()), 5),
            })
    results.sort(key=lambda r: r["comp_mae"])
    return results


def _evaluate_gate(hard, shrunk):
    """Evaluate the three MAE-based acceptance-gate conditions (the fourth,
    test_engine.py passing, is checked by running that suite separately)."""
    checks = []

    checks.append({
        "name": "overall LOO WA MAE:  shrunk <= hard",
        "hard": hard["overall_wa_mae"], "shrunk": shrunk["overall_wa_mae"],
        "pass": shrunk["overall_wa_mae"] <= hard["overall_wa_mae"] + 1e-9,
    })

    hb = hard["by_bucket"]["cluster 2<=n<6"]
    sb = shrunk["by_bucket"]["cluster 2<=n<6"]
    ok2 = (hb["mae"] is not None and sb["mae"] is not None
           and sb["mae"] < hb["mae"] - 1e-9)
    checks.append({
        "name": "small-n bucket (cluster 2<=n<6):  shrunk strictly better",
        "hard": hb["mae"], "shrunk": sb["mae"], "pass": bool(ok2),
    })

    hr = hard["by_bucket"]["cluster n>=6"]
    sr = shrunk["by_bucket"]["cluster n>=6"]
    ok3 = (hr["mae"] is not None and sr["mae"] is not None
           and sr["mae"] <= hr["mae"] + 0.03 + 1e-9)
    checks.append({
        "name": "data-rich bucket (cluster n>=6):  shrunk within +0.03 MAE",
        "hard": hr["mae"], "shrunk": sr["mae"], "pass": bool(ok3),
    })

    return {"checks": checks, "all_pass": all(c["pass"] for c in checks)}


def compare_modes(books, gw, gcw, k_author, k_genre, folds=None):
    """Run the SAME LOO sweep under both analog modes and evaluate the gate."""
    if folds is None:
        folds = _build_folds(books)
    hard = _summarize(books, *_loo_sweep(folds, books, gw, gcw, "hard"))
    shrunk = _summarize(books, *_loo_sweep(folds, books, gw, gcw, "shrunk",
                                           k_author, k_genre))
    return {"hard": hard, "shrunk": shrunk, "gate": _evaluate_gate(hard, shrunk),
            "k_author": k_author, "k_genre": k_genre}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def _fmt(x, w=8, p=4):
    return (" " * (w - 1) + "-") if x is None else f"{x:>{w}.{p}f}"


def _write_residuals_cli(books, gw, gcw, out_path):
    """--write-residuals entry: build + persist the table, then print the
    per-bucket summary and in-sample coverage (the deliverable numbers)."""
    print("=" * 72)
    print("CONFORMAL RESIDUAL TABLE  (shrunk LOO — offline snapshot for intervals)")
    print("=" * 72)
    print(f"{len(books)} books. Running leave-one-out under ANALOG_MODE='shrunk' "
          f"(K_AUTHOR={pe.K_AUTHOR}, K_GENRE={pe.K_GENRE})...")
    table = write_residuals(out_path=out_path, books=books, gw=gw, gcw=gcw)
    print(f"\nWrote {out_path}")
    print(f"  engine_hash : {table['engine_hash']}")
    print(f"  generated   : {table['generated_at']}")

    print("\n" + "-" * 72)
    print("PER-BUCKET  (half = 80th pct |resid|;  q10/q90/mean are SIGNED)")
    print("-" * 72)
    print(f"  {'bucket':<20}{'n':>4}{'half':>8}{'q10':>8}{'q90':>8}{'mean':>8}"
          f"  pooled")
    print("  " + "-" * 64)
    for b in BUCKET_ORDER:
        bi = table["buckets"][b]
        pooled = ("" if not bi["pooled"]
                  else f"yes ← +{bi['pooled_with']} (pool n={bi['pool_n']})")
        print(f"  {b:<20}{bi['n']:>4}{_fmt(bi['half_width'])}{_fmt(bi['q10'])}"
              f"{_fmt(bi['q90'])}{_fmt(bi['mean_residual'])}  {pooled}")

    cov = table["coverage"]
    ov = cov["overall"]
    band = "PASS" if (ov is not None and 0.72 <= ov <= 0.88) else "OUT OF BAND"
    print("\n" + "-" * 72)
    print("IN-SAMPLE COVERAGE  (share inside the bucket's 80% interval)")
    print("-" * 72)
    print(f"  overall : {'-' if ov is None else f'{ov:.1%}'}   "
          f"[{band}]   (gate band 72–88%)")
    for b in BUCKET_ORDER:
        cb, nb = cov["by_bucket"][b], cov["by_bucket_n"][b]
        print(f"    {b:<20} n={nb:>3}  "
              f"{'-' if cb is None else f'{cb:.1%}':>6}   (informational)")
    print("=" * 72)


def main():
    import argparse
    import db_loader

    ap = argparse.ArgumentParser(
        description="Leave-one-out validation: the hard-vs-shrunk A/B report "
                    "(default), or --write-residuals to persist the conformal "
                    "residual table the prediction intervals are served from.")
    ap.add_argument("--write-residuals", action="store_true",
                    help="Run the shrunk LOO sweep and write the residual table "
                         "to --out (default calibration/residuals.json).")
    ap.add_argument("--out", default=os.path.join("calibration", "residuals.json"),
                    help="Destination for --write-residuals.")
    args = ap.parse_args()

    books, gw, gcw = db_loader.load_from_db()

    if args.write_residuals:
        _write_residuals_cli(books, gw, gcw, args.out)
        return

    n = len(books)

    print("=" * 72)
    print("ANALOG-MODE A/B — HARD FALLBACK vs EMPIRICAL-BAYES SHRINKAGE (LOO)")
    print("=" * 72)
    print(f"{n} books. Each is removed, the engine refit on the other {n-1},")
    print("then predicted from scratch. All MAEs below are on UNSEEN books.")
    print(f"Live engine default: predict_engine.ANALOG_MODE = '{pe.ANALOG_MODE}'\n")

    print("Building leave-one-out folds (one refit per book)...")
    folds = _build_folds(books)

    # --- tune k --------------------------------------------------------------
    print(f"Tuning k over grid {DEFAULT_K_GRID} "
          f"({len(DEFAULT_K_GRID)**2} cells x {n} folds)...")
    ranked = tune_k(folds, books, gw, gcw)
    best, second = ranked[0], ranked[1]
    ka, kg = best["k_author"], best["k_genre"]

    print("\n" + "-" * 72)
    print("TUNED SHRINKAGE CONSTANTS (min overall LOO component MAE)")
    print("-" * 72)
    print(f"  best     : K_AUTHOR={best['k_author']:<4} K_GENRE={best['k_genre']:<4}"
          f"  comp MAE={best['comp_mae']:.4f}  WA MAE={best['wa_mae']:.4f}")
    print(f"  2nd best : K_AUTHOR={second['k_author']:<4} K_GENRE={second['k_genre']:<4}"
          f"  comp MAE={second['comp_mae']:.4f}  WA MAE={second['wa_mae']:.4f}"
          f"   (sensitivity check)")
    print("  NOTE: k is tuned and scored on the same LOO sweep, so the winning")
    print("        MAE is mildly optimistic. The 2nd-best cell is close, which")
    print("        indicates the choice is not on a knife-edge.")

    # --- A/B compare ---------------------------------------------------------
    cmp = compare_modes(books, gw, gcw, ka, kg, folds=folds)
    hard, shrunk = cmp["hard"], cmp["shrunk"]

    print("\n" + "-" * 72)
    print(f"OVERALL  (shrunk uses K_AUTHOR={ka}, K_GENRE={kg})")
    print("-" * 72)
    print(f"  {'metric':<26}{'hard':>10}{'shrunk':>10}{'change':>10}")
    print("  " + "-" * 56)

    def _row(label, h, s, better="down"):
        d = (h - s) if better == "down" else (s - h)
        print(f"  {label:<26}{h:>10.4f}{s:>10.4f}{d:>+10.4f}")

    _row("WA MAE", hard["overall_wa_mae"], shrunk["overall_wa_mae"])
    _row("component MAE", hard["overall_comp_mae"], shrunk["overall_comp_mae"])
    print(f"  {'within 0.5':<26}{hard['within_0_5']:>10.0%}"
          f"{shrunk['within_0_5']:>10.0%}")
    print(f"  {'within 1.0':<26}{hard['within_1_0']:>10.0%}"
          f"{shrunk['within_1_0']:>10.0%}")
    print(f"  (naive baseline WA MAE = {hard['naive_mae']:.4f})")

    print("\n" + "-" * 72)
    print("WA MAE BY DATA DENSITY  (the shrinkage should win in the small-n row)")
    print("-" * 72)
    print(f"  {'bucket':<20}{'n':>5}{'hard':>10}{'shrunk':>10}{'change':>10}")
    print("  " + "-" * 55)
    for b in BUCKET_ORDER:
        hb, sb = hard["by_bucket"][b], shrunk["by_bucket"][b]
        cnt = hb["n"]
        chg = (None if hb["mae"] is None or sb["mae"] is None
               else hb["mae"] - sb["mae"])
        print(f"  {b:<20}{cnt:>5}{_fmt(hb['mae'])}{_fmt(sb['mae'])}"
              f"{('' if chg is None else f'{chg:>+10.4f}')}")

    print("\n" + "-" * 72)
    print("PER-GENRE WA MAE  (hard -> shrunk)")
    print("-" * 72)
    hg = {r["genre"]: r for r in hard["per_genre"]}
    sg = {r["genre"]: r for r in shrunk["per_genre"]}
    print(f"  {'genre':<30}{'n':>4}{'hard':>9}{'shrunk':>9}{'change':>9}")
    print("  " + "-" * 61)
    for g in sorted(hg, key=lambda x: hg[x]["mae"]):
        h, s = hg[g]["mae"], sg[g]["mae"]
        print(f"  {g:<30}{hg[g]['n']:>4}{h:>9.3f}{s:>9.3f}{h - s:>+9.3f}")

    # --- gate ----------------------------------------------------------------
    print("\n" + "=" * 72)
    print("ACCEPTANCE GATE")
    print("=" * 72)
    for c in cmp["gate"]["checks"]:
        status = "PASS" if c["pass"] else "FAIL"
        print(f"  [{status}] {c['name']}")
        print(f"           hard={_fmt(c['hard'], 7, 4).strip()}  "
              f"shrunk={_fmt(c['shrunk'], 7, 4).strip()}")
    print("  [ -- ] test_engine.py must stay green (run it separately)")
    print()
    if cmp["gate"]["all_pass"]:
        print("  => MAE gate PASSES. If test_engine.py is green, flip")
        print(f"     predict_engine.ANALOG_MODE to 'shrunk' with "
              f"K_AUTHOR={ka}, K_GENRE={kg}.")
    else:
        print("  => MAE gate FAILS. Keep predict_engine.ANALOG_MODE = 'hard'.")
        print("     Keep this harness + report; revert nothing else.")
    print("=" * 72)


if __name__ == "__main__":
    main()
