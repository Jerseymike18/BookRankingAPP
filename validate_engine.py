"""
validate_engine.py
==================
PHASE 2: Honest leave-one-out (LOO) validation of the full prediction engine.

THE QUESTION THIS ANSWERS
-------------------------
"How accurate is the engine on a book it has NEVER seen?" -- and, more usefully,
"which parts of it are real signal versus the model just recognising books it
already memorised?"

HOW IT STAYS HONEST
-------------------
For each book we:
  1. REMOVE it from the dataset.
  2. Refit the regression + per-genre biases on the OTHER 124 books only.
  3. Predict the removed book from scratch (autonomous component estimate ->
     category averages -> regression -> bias -> blend), exactly as the engine
     would for a brand-new book.
  4. Compare the prediction to the book's real WA.
This "refit without the test book" step is what makes the number trustworthy.
If we let each book sit in its own training data, accuracy would look better
than it really is -- that's the trap most home-grown models fall into.

WHAT IT REPORTS
---------------
  A. Headline WA accuracy of the autonomous engine vs. two baselines.
  B. Per-genre WA accuracy (where is it strong / weak / untrustworthy).
  C. Per-COMPONENT accuracy -- which of your 19 components are predictable and
     which are essentially noise. (This is the map of where your scoring carries
     signal, and where the future research layer has the most to add.)
  D. Does the per-genre BIAS correction actually help on held-out books, or is
     it just memorising residuals?

HOW TO RUN (Thonny): set WORKBOOK below to your real path, press Run.
Needs predict_engine.py in the SAME folder (it imports from it).
"""

import numpy as np
import pandas as pd

# Reuse the engine we already built & validated.
import predict_engine as pe

# IMPORTANT: point this at the SAME workbook path that works in predict_engine.
# If predict_engine.py already has the right WORKBOOK path, this just reuses it.
WORKBOOK = pe.WORKBOOK


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
                upstream, apply_bias=True):
    """
    Predict a single held-out book from scratch using only the training set.
    Returns (predicted_WA, estimated_components_dict).
    """
    author, genre = test_row["Author"], test_row["Genre"]
    est, _, _ = pe.estimate_components(train_books, author, genre, upstream)
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
# Main LOO loop — callable as a function or as __main__
# ---------------------------------------------------------------------------

def run_loo(books=None, gw=None, gcw=None):
    """
    Run the leave-one-out validation and return structured results.
    If books/gw/gcw are not supplied, loads from the Excel workbook.
    Computation is slow (~126 regression refits) — do not call on every request.
    """
    if books is None:
        books, gw, gcw = pe.load_everything(WORKBOOK)

    n = len(books)
    naive = float(np.abs(books["WA"] - books["WA"].mean()).mean())

    wa_pred = np.full(n, np.nan)
    wa_pred_nobias = np.full(n, np.nan)
    comp_err = {c: [] for c in pe.components_of(books)}

    idx = books.index.tolist()
    for pos, i in enumerate(idx):
        test_row = books.loc[i]
        train = books.drop(i)
        coeffs, r2, resid_sd, ginfo, upstream = fit_on(train)

        wa_b, est = predict_one(test_row, train, gw, gcw, coeffs, resid_sd,
                                ginfo, upstream, apply_bias=True)
        wa_nb, _ = predict_one(test_row, train, gw, gcw, coeffs, resid_sd,
                               ginfo, upstream, apply_bias=False)
        wa_pred[pos] = wa_b
        wa_pred_nobias[pos] = wa_nb

        for c in pe.components_of(books):
            actual = test_row[c]
            if actual is not None and not (isinstance(actual, float) and np.isnan(actual)):
                comp_err[c].append(abs(est[c] - actual))

    actual_wa = books["WA"].values

    def mae(p):
        m = ~np.isnan(p)
        return float(np.abs(p[m] - actual_wa[m]).mean())

    def within(p, tol):
        m = ~np.isnan(p)
        return float((np.abs(p[m] - actual_wa[m]) <= tol).mean())

    mb = mae(wa_pred)
    mnb = mae(wa_pred_nobias)
    improvement = (naive - mb) / naive * 100 if naive else 0.0

    per_genre = []
    tmp = books.copy()
    tmp["pred"] = wa_pred
    for g, sub in tmp.groupby("Genre"):
        e = float(np.abs(sub["pred"] - sub["WA"]).mean())
        ng = len(sub)
        verdict = ("thin" if ng < 5 else
                   "strong" if e < 0.6 else "okay" if e < 0.9 else "weak")
        per_genre.append({"genre": g, "n": ng, "mae": round(e, 4), "verdict": verdict})
    per_genre.sort(key=lambda x: x["mae"])

    per_component = []
    for c in pe.components_of(books):
        errs = comp_err[c]
        if errs:
            m = float(np.mean(errs))
            verdict = ("strong signal" if m < 0.9 else
                       "moderate" if m < 1.15 else "weak / noisy")
            per_component.append({"component": c, "mae": round(m, 4),
                                  "n": len(errs), "verdict": verdict})
    per_component.sort(key=lambda x: x["mae"])

    return {
        "n_books": n,
        "naive_mae": round(naive, 4),
        "engine_mae": round(mb, 4),
        "within_0_5": round(within(wa_pred, 0.5), 4),
        "within_1_0": round(within(wa_pred, 1.0), 4),
        "improvement_pct": round(improvement, 1),
        "bias_mae": round(mb, 4),
        "no_bias_mae": round(mnb, 4),
        "bias_helps": bool(mb < mnb),
        "bias_delta": round(mnb - mb, 4),
        "per_genre": per_genre,
        "per_component": per_component,
    }


def main():
    result = run_loo()
    n = result["n_books"]
    print("=" * 68)
    print("PHASE 2 — HONEST LEAVE-ONE-OUT VALIDATION OF THE FULL ENGINE")
    print("=" * 68)
    print(f"{n} books. Each is removed, the engine refit on the other {n-1},")
    print("then predicted from scratch. Accuracy below is on UNSEEN books.\n")

    print("-" * 68)
    print("A. HEADLINE WA ACCURACY (autonomous engine, books unseen)")
    print("-" * 68)
    print(f"  Naive baseline (guess the mean) MAE : {result['naive_mae']:.3f}")
    print(f"  Full engine                     MAE : {result['engine_mae']:.3f}")
    print(f"     within 0.5 : {result['within_0_5']:.0%}    "
          f"within 1.0 : {result['within_1_0']:.0%}")
    print(f"  => {result['improvement_pct']:.0f}% better than guessing the average.\n")

    print("-" * 68)
    print("D. DOES THE PER-GENRE BIAS CORRECTION HELP ON UNSEEN BOOKS?")
    print("-" * 68)
    print(f"  With bias correction    MAE : {result['bias_mae']:.4f}")
    print(f"  Without bias correction MAE : {result['no_bias_mae']:.4f}")
    if result["bias_helps"]:
        d = result["bias_delta"]
        print(f"  => Bias correction helps by {d:.4f} MAE on held-out books\n")
    elif result["bias_delta"] < 0:
        print(f"  => Bias correction HURTS by {-result['bias_delta']:.4f} out-of-sample\n")
    else:
        print("  => No measurable difference either way.\n")

    print("-" * 68)
    print("B. PER-GENRE WA ACCURACY")
    print("-" * 68)
    print(f"  {'Genre':<30}{'n':>4}{'MAE':>9}{'verdict':>20}")
    print("  " + "-" * 60)
    for row in result["per_genre"]:
        print(f"  {row['genre']:<30}{row['n']:>4}{row['mae']:>9.3f}{row['verdict']:>20}")
    print()

    print("-" * 68)
    print("C. PER-COMPONENT ACCURACY  (which scores carry real signal)")
    print("-" * 68)
    print(f"  {'Component':<24}{'MAE':>8}{'n':>6}{'verdict':>20}")
    print("  " + "-" * 56)
    for row in result["per_component"]:
        print(f"  {row['component']:<24}{row['mae']:>8.3f}{row['n']:>6}{row['verdict']:>20}")
    print()


if __name__ == "__main__":
    main()
