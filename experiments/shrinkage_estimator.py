"""
shrinkage_estimator.py
======================
PHASE 3, IMPROVEMENT 1: A smarter component estimator (hierarchical shrinkage),
A/B-tested against the current flat author/genre mean using the same honest
leave-one-out harness from Phase 2.

THE PROBLEM WE'RE FIXING
------------------------
Phase 2 showed the engine's accuracy bottleneck is the COMPONENT ESTIMATION
step. The current method picks ONE level -- author mean if you've read >=2 by
them, else genre mean -- and uses it flat. That has two failure modes:
  * Bimodal authors (Sanderson: standalones ~4, Stormlight ~9). His flat mean
    (~7.3) predicts every Sanderson book as 7.3 -- badly wrong for both ends.
  * Small-sample authors (2 books) get a noisy mean trusted as if it were solid.

THE FIX: HIERARCHICAL SHRINKAGE
-------------------------------
Instead of trusting one level fully, BLEND three estimates for each component:
      author mean  (specific but noisy when n is small)
      genre mean   (stable, less personal)
      global mean  (very stable, not personal at all)
Each level's weight grows with how much data supports it. Formally, for a level
with n observations:
      weight  =  n / (n + K)
where K is a "shrinkage constant" -- how much evidence you demand before
trusting that level. Small n -> weight shrinks toward 0 -> we fall back to the
broader level. Large n -> weight -> 1 -> we trust the specific level.

This is the principled version of your fallback ladder: instead of a hard
"use author OR genre", every prediction is a smooth blend that leans specific
when the data is there and backs off to safe when it isn't.

WHAT THIS SCRIPT DOES
---------------------
1. Defines the shrinkage estimator.
2. Re-runs the FULL Phase-2 LOO validation with it.
3. Prints the new MAE next to the 0.812 baseline so you can see, honestly,
   whether it helped -- overall, per genre, and per component.

Run in Thonny (needs predict_engine.py + validate_engine.py in same folder).
"""

import numpy as np
import pandas as pd
import predict_engine as pe
import validate_engine as ve

WORKBOOK = pe.WORKBOOK

# Shrinkage constants: how many books before we "trust" a level.
# Tunable -- we'll test sensitivity below. Higher K = more shrinkage toward
# the broader (safer) level.
K_AUTHOR = 2.0   # trust an author's own profile once you've read a couple
K_GENRE = 10.0   # genres need solid support before overriding the global prior
# (These were chosen by the sensitivity sweep. Treat ~0.76 as the honest MAE;
#  the exact 0.759 is mildly optimistic because the constants were tuned on the
#  same data we measure with.)


def shrunk_components(train_books, author, genre, upstream):
    """
    Estimate the 19 components by hierarchically shrinking
    author -> genre -> global, per component, weighted by support size.
    """
    global_means = {c: float(train_books[c].dropna().mean()) for c in pe.components_of(train_books)}
    by_author = train_books[train_books["Author"] == author]
    by_genre = train_books[train_books["Genre"] == genre]

    est = {}
    for c in pe.components_of(train_books):
        g_vals = by_genre[c].dropna()
        a_vals = by_author[c].dropna()
        n_g, n_a = len(g_vals), len(a_vals)

        # Start from the global mean as the base prior.
        value = global_means[c]

        # Blend in the genre mean, weighted by genre support.
        if n_g > 0:
            w_g = n_g / (n_g + K_GENRE)
            value = w_g * float(g_vals.mean()) + (1 - w_g) * value

        # Blend in the author mean on top, weighted by author support.
        if n_a > 0:
            w_a = n_a / (n_a + K_AUTHOR)
            value = w_a * float(a_vals.mean()) + (1 - w_a) * value

        est[c] = value

    # Same Section-7 upstream refinement, using v2's {coef, drivers} format.
    for target, model in upstream.items():
        coef, drivers = model["coef"], model["drivers"]
        if all(d in est for d in drivers):
            pred = coef[0] + sum(coef[k + 1] * est[drivers[k]] for k in range(len(drivers)))
            est[target] = 0.5 * est[target] + 0.5 * float(pred)
    return est


def predict_one_shrunk(test_row, train_books, gw, gcw, coeffs, ginfo, upstream):
    author, genre = test_row["Author"], test_row["Genre"]
    est = shrunk_components(train_books, author, genre, upstream)
    wcats = pe.components_to_wcats(est, genre, gcw)
    wa_model = pe.regression_wa(coeffs, wcats["Story"], wcats["Character"],
                                wcats["Aesthetics"], wcats["Theme"])
    g = ginfo.get(genre, {"bias": 0.0, "trust": 0.0})
    wa_corrected = wa_model + g["bias"]
    analog = train_books[train_books["Author"] == author]["WA"].dropna()
    if len(analog) < 2:
        analog = train_books[train_books["Genre"] == genre]["WA"].dropna()
    analog_mean = float(analog.mean()) if len(analog) else wa_corrected
    trust = g["trust"]
    wa_final = trust * wa_corrected + (1 - trust) * analog_mean
    return wa_final, est


def run_loo(books, gw, gcw, estimator):
    """Generic LOO loop; estimator is 'flat' or 'shrunk'."""
    n = len(books)
    wa_pred = np.full(n, np.nan)
    comp_err = {c: [] for c in pe.components_of(books)}
    idx = books.index.tolist()
    for pos, i in enumerate(idx):
        test_row = books.loc[i]
        train = books.drop(i)
        coeffs, r2, resid_sd, ginfo, upstream = ve.fit_on(train)
        if estimator == "flat":
            wa, est = ve.predict_one(test_row, train, gw, gcw, coeffs,
                                     resid_sd, ginfo, upstream, apply_bias=True)
        else:
            wa, est = predict_one_shrunk(test_row, train, gw, gcw, coeffs,
                                         ginfo, upstream)
        wa_pred[pos] = wa
        for c in pe.components_of(books):
            a = test_row[c]
            if a is not None and not (isinstance(a, float) and np.isnan(a)):
                comp_err[c].append(abs(est[c] - a))
    return wa_pred, comp_err


def main():
    books, gw, gcw = pe.load_everything(WORKBOOK)
    actual = books["WA"].values
    naive = float(np.abs(actual - actual.mean()).mean())

    print("=" * 68)
    print("IMPROVEMENT 1: HIERARCHICAL SHRINKAGE  (A/B vs. flat baseline)")
    print("=" * 68)
    print(f"Baseline = current flat author/genre mean.  Naive = {naive:.3f}\n")
    print(f"Shrinkage constants: K_AUTHOR={K_AUTHOR}, K_GENRE={K_GENRE}\n")

    flat_wa, flat_ce = run_loo(books, gw, gcw, "flat")
    shr_wa, shr_ce = run_loo(books, gw, gcw, "shrunk")

    def mae(p):
        m = ~np.isnan(p); return float(np.abs(p[m] - actual[m]).mean())
    def within(p, t):
        m = ~np.isnan(p); return float((np.abs(p[m] - actual[m]) <= t).mean())

    print("-" * 68)
    print("OVERALL WA ACCURACY")
    print("-" * 68)
    print(f"  {'':<18}{'MAE':>8}{'within0.5':>12}{'within1.0':>12}")
    print(f"  {'Flat (baseline)':<18}{mae(flat_wa):>8.4f}"
          f"{within(flat_wa,0.5):>11.0%}{within(flat_wa,1.0):>11.0%}")
    print(f"  {'Shrinkage':<18}{mae(shr_wa):>8.4f}"
          f"{within(shr_wa,0.5):>11.0%}{within(shr_wa,1.0):>11.0%}")
    d = mae(flat_wa) - mae(shr_wa)
    verdict = ("IMPROVES" if d > 0.0005 else "HURTS" if d < -0.0005 else "no change")
    print(f"\n  => Shrinkage {verdict} WA MAE by {d:+.4f} "
          f"({d/mae(flat_wa)*100:+.1f}%)\n")

    print("-" * 68)
    print("PER-COMPONENT MAE  (flat -> shrunk, sorted by improvement)")
    print("-" * 68)
    print(f"  {'Component':<24}{'flat':>8}{'shrunk':>9}{'change':>9}")
    print("  " + "-" * 50)
    rows = []
    for c in pe.components_of(books):
        if flat_ce[c] and shr_ce[c]:
            f, s = np.mean(flat_ce[c]), np.mean(shr_ce[c])
            rows.append((c, f, s, f - s))
    for c, f, s, ch in sorted(rows, key=lambda x: -x[3]):
        arrow = "better" if ch > 0.005 else "worse" if ch < -0.005 else "same"
        print(f"  {c:<24}{f:>8.3f}{s:>9.3f}{ch:>+9.3f}  {arrow}")
    print()

    # Per-genre comparison
    print("-" * 68)
    print("PER-GENRE WA MAE  (flat -> shrunk)")
    print("-" * 68)
    tmp = books.copy(); tmp["flat"] = flat_wa; tmp["shr"] = shr_wa
    print(f"  {'Genre':<30}{'n':>4}{'flat':>8}{'shrunk':>9}{'change':>9}")
    print("  " + "-" * 58)
    for g, sub in tmp.groupby("Genre"):
        f = np.abs(sub["flat"] - sub["WA"]).mean()
        s = np.abs(sub["shr"] - sub["WA"]).mean()
        print(f"  {g:<30}{len(sub):>4}{f:>8.3f}{s:>9.3f}{f-s:>+9.3f}")
    print()
    print("Note: shrinkage should help most where the flat method was weak --")
    print("bimodal authors and small-sample genres. Components that are already")
    print("author-stable (Prose) may barely move; that's expected.")


if __name__ == "__main__":
    main()
