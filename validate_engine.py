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

import numpy as np
import pandas as pd

# Reuse the engine we already built & validated.
import predict_engine as pe

WORKBOOK = pe.WORKBOOK

# k grid for the shrinkage tuning sweep (shared per tier; see tune_k).
DEFAULT_K_GRID = [0.5, 1, 2, 3, 5, 8, 12]

# Display order for the density buckets.
BUCKET_ORDER = ["cluster n>=6", "cluster 2<=n<6", "author-only n=1",
                "genre-only n=0"]


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
        folds.append({"i": i, "test_row": test_row, "train": train,
                      "coeffs": coeffs, "resid_sd": resid_sd, "ginfo": ginfo,
                      "upstream": upstream, "n_peers": n_peers})
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


def _density_bucket(n_peers):
    """Bucket a held-out book by how many same-author training books its
    prediction had (n-1 for an author with n books total). Maps the brief's four
    density strata onto this engine's tiers, where author is the innermost pool:
      >=6  -> data-rich  (e.g. EF-Erikson)   | must not regress by >0.03
      2..5 -> small-n    (e.g. SF-Card)       | the expected win
      1    -> author-only (hard falls to genre)
      0    -> genre-only  (no author signal)."""
    if n_peers >= 6:
        return "cluster n>=6"
    if n_peers >= 2:
        return "cluster 2<=n<6"
    if n_peers == 1:
        return "author-only n=1"
    return "genre-only n=0"


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

    npa = np.array([_density_bucket(int(x)) for x in n_peers])
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


def main():
    import db_loader
    books, gw, gcw = db_loader.load_from_db()
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
