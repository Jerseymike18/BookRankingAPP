"""
correlation_experiment.py
=========================
TRACK 2: can the component CORRELATION STRUCTURE (from CorrHeatmap) lower
prediction error beyond the 0.837 the richer-prompt + correction already gets?

THE IDEA
--------
Your component scores are highly intercorrelated (Depth2~Integration r=0.93,
Prose~Narration 0.91, Depth~Motivations 0.90 — even the weakest pair is 0.53).
That means the 14 components don't move independently. So when the LLM's
estimate of one component is noisy, the OTHER components it's more confident
about carry information about it. We can exploit this by predicting each
component from the others ("implied value") and blending that with the direct
estimate — pulling noisy estimates toward what their correlated neighbors imply.

THE HONEST TEST
---------------
The correction layer (0.837) may ALREADY capture some of this. So we don't test
correlation-smoothing against raw LLM — we test whether it lowers error when
layered ON TOP of the full correction. Everything leave-one-out. Only adopt it
if it beats 0.837.

We test two places to apply the correlation smoothing:
  (a) smooth the RAW LLM components, THEN correct  (smoothing as pre-processing)
  (b) correct first, THEN smooth the corrected components (as post-processing)
...across a few blend weights, so we see if there's a real effect or noise.

HOW TO RUN (Thonny): press Run. No API calls; uses llm_scores_richer.json.
"""

import json
import numpy as np
import pandas as pd
from numpy.linalg import lstsq
import predict_engine as pe

CACHE = "llm_scores_richer.json"
LIVE = ["Plot", "Entertainment", "Action", "Ending", "Depth",
        "Emotional Impact", "Motivations", "Prose", "Narration",
        "Insights", "Thought-Provokingness", "Depth2", "Integration", "Originality"]
K_GENRE = 6.0
K_AUTHOR = 4.0


def build_pairs():
    books, gw, gcw = pe.load_everything()
    cache = json.load(open(CACHE))
    rows = []
    for _, b in books.iterrows():
        if b["Book"] not in cache:
            continue
        s = cache[b["Book"]]["scores"]
        rec = {"Book": b["Book"], "Genre": b["Genre"], "Author": b["Author"]}
        ok = True
        for c in LIVE:
            yv, lv = b[c], s.get(c)
            if yv is None or lv is None or (isinstance(yv, float) and np.isnan(yv)):
                ok = False; break
            rec["you_" + c] = float(yv); rec["llm_" + c] = float(lv)
        if ok:
            rows.append(rec)
    return pd.DataFrame(rows).reset_index(drop=True)


def fit_line(x, y):
    if len(x) < 3 or np.std(x) < 1e-9:
        return None
    b, a = np.polyfit(x, y, 1)
    return a, b


def correct_components(train, b, source_prefix="llm_"):
    """Full author_genre correction for one book's components."""
    out = {}
    for c in LIVE:
        llm = b[source_prefix + c]
        dev_all = (train["you_" + c] - train["llm_" + c])
        global_dev = dev_all.mean()
        g = train[train["Genre"] == b["Genre"]]
        n_g = len(g)
        line = fit_line(g["llm_" + c].values, g["you_" + c].values) if n_g >= 3 else None
        gpred = (line[0] + line[1] * llm) if line else (llm + (
            (g["you_" + c] - g["llm_" + c]).mean() if n_g else global_dev))
        global_pred = llm + global_dev
        wg = n_g / (n_g + K_GENRE)
        genre_pred = wg * gpred + (1 - wg) * global_pred
        au = train[train["Author"] == b["Author"]]
        n_a = len(au)
        if n_a > 0:
            adev = (au["you_" + c] - au["llm_" + c]).mean()
            wa = n_a / (n_a + K_AUTHOR)
            out[c] = wa * (llm + adev) + (1 - wa) * genre_pred
        else:
            out[c] = genre_pred
    return out


def precompute_corr_models(train):
    """For each component, fit your_c ~ LLM(other components) on the training set."""
    models = {}
    for c in LIVE:
        others = [o for o in LIVE if o != c]
        X = np.column_stack([np.ones(len(train))] + [train["llm_" + o].values for o in others])
        y = train["you_" + c].values
        coef, *_ = lstsq(X, y, rcond=None)
        models[c] = (others, coef)
    return models


def implied_value(models, b, c, source_prefix="llm_"):
    others, coef = models[c]
    x = np.array([1.0] + [b[source_prefix + o] for o in others])
    return float(x @ coef)


def mae(preds, df):
    errs = []
    for i in range(len(df)):
        for c in LIVE:
            errs.append(abs(preds[i][c] - df.iloc[i]["you_" + c]))
    return np.mean(errs)


def main():
    df = build_pairs()
    n = len(df)
    print("=" * 60)
    print(f"CORRELATION-STRUCTURE EXPERIMENT (n={n}, leave-one-out)")
    print("=" * 60)

    # Baseline: full correction, no smoothing
    base_preds = []
    corr_models_loo = []
    for i in range(n):
        tr = df.drop(df.index[i])
        b = df.iloc[i]
        base_preds.append(correct_components(tr, b))
        corr_models_loo.append(precompute_corr_models(tr))
    base = mae(base_preds, df)
    print(f"  Baseline (correction only)        : {base:.4f}")
    print()

    # (a) smooth raw THEN correct, across blend weights
    print("  (a) smooth raw LLM, then correct:")
    for blend in [0.2, 0.4, 0.6]:
        preds = []
        for i in range(n):
            tr = df.drop(df.index[i]); b = df.iloc[i].copy()
            models = corr_models_loo[i]
            # build a smoothed copy of the LLM components
            for c in LIVE:
                imp = implied_value(models, b, c)
                b["llm_" + c] = blend * imp + (1 - blend) * b["llm_" + c]
            preds.append(correct_components(tr, b))
        print(f"    blend={blend:.1f}: {mae(preds, df):.4f}")
    print()

    # (b) correct first, THEN smooth the corrected components
    # (smoothing model is on your-vs-llm; for post we smooth toward implied-from-corrected)
    print("  (b) correct, then smooth corrected components:")
    for blend in [0.2, 0.4, 0.6]:
        preds = []
        for i in range(n):
            tr = df.drop(df.index[i]); b = df.iloc[i]
            corrected = correct_components(tr, b)
            models = corr_models_loo[i]
            # implied value from the CORRECTED components of this book
            cb = {("llm_" + c): corrected[c] for c in LIVE}
            smoothed = {}
            for c in LIVE:
                others, coef = models[c]
                x = np.array([1.0] + [cb["llm_" + o] for o in others])
                imp = float(x @ coef)
                smoothed[c] = blend * imp + (1 - blend) * corrected[c]
            preds.append(smoothed)
        print(f"    blend={blend:.1f}: {mae(preds, df):.4f}")
    print()
    print("=" * 60)
    print("VERDICT")
    print("=" * 60)
    print(f"  Baseline to beat: {base:.4f}")
    print("  If no smoothing variant beats it by a clear margin (>0.005),")
    print("  the correction layer already captures the correlation signal —")
    print("  don't add smoothing. If one clearly wins, adopt it.")


if __name__ == "__main__":
    main()
