"""
correlation_verify.py
=====================
Rigorous re-test of the correlation-smoothing gain, with the correlation models
fit STRICTLY leave-one-out (the previous experiment trained them on the full
set, making the smoothing side very slightly optimistic). If the ~0.011 gain
survives here, it's real and we ship it. If it shrinks to nothing, we don't.

Only tests the winning variant: smooth raw LLM (blend 0.2) THEN correct.
Compares strict-LOO smoothing against the correction-only baseline.

HOW TO RUN (Thonny): press Run. Uses llm_scores_richer.json, no API calls.
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
BLEND = 0.2


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


def correct_components(train, b):
    out = {}
    for c in LIVE:
        llm = b["llm_" + c]
        global_dev = (train["you_" + c] - train["llm_" + c]).mean()
        g = train[train["Genre"] == b["Genre"]]
        n_g = len(g)
        line = fit_line(g["llm_" + c].values, g["you_" + c].values) if n_g >= 3 else None
        gpred = (line[0] + line[1] * llm) if line else (llm + (
            (g["you_" + c] - g["llm_" + c]).mean() if n_g else global_dev))
        wg = n_g / (n_g + K_GENRE)
        genre_pred = wg * gpred + (1 - wg) * (llm + global_dev)
        au = train[train["Author"] == b["Author"]]
        n_a = len(au)
        if n_a > 0:
            adev = (au["you_" + c] - au["llm_" + c]).mean()
            wa = n_a / (n_a + K_AUTHOR)
            out[c] = wa * (llm + adev) + (1 - wa) * genre_pred
        else:
            out[c] = genre_pred
    return out


def corr_models(train):
    """your_c ~ LLM(others), fit on the given training set only."""
    m = {}
    for c in LIVE:
        others = [o for o in LIVE if o != c]
        X = np.column_stack([np.ones(len(train))] + [train["llm_" + o].values for o in others])
        coef, *_ = lstsq(X, train["you_" + c].values, rcond=None)
        m[c] = (others, coef)
    return m


def mae(preds, df):
    e = []
    for i in range(len(df)):
        for c in LIVE:
            e.append(abs(preds[i][c] - df.iloc[i]["you_" + c]))
    return np.mean(e)


def main():
    df = build_pairs()
    n = len(df)
    print("=" * 60)
    print(f"STRICT LOO VERIFICATION (n={n}, blend={BLEND})")
    print("=" * 60)

    base_preds, smooth_preds = [], []
    for i in range(n):
        tr = df.drop(df.index[i])
        b = df.iloc[i]
        # baseline: correct only
        base_preds.append(correct_components(tr, b))
        # smoothing: fit corr models on TRAIN ONLY (strict LOO), smooth, then correct
        models = corr_models(tr)
        bs = b.copy()
        for c in LIVE:
            others, coef = models[c]
            x = np.array([1.0] + [b["llm_" + o] for o in others])
            imp = float(x @ coef)
            bs["llm_" + c] = BLEND * imp + (1 - BLEND) * b["llm_" + c]
        smooth_preds.append(correct_components(tr, bs))

    base = mae(base_preds, df)
    smooth = mae(smooth_preds, df)
    print(f"  Correction only (baseline)     : {base:.4f}")
    print(f"  + correlation smoothing (strict): {smooth:.4f}")
    print(f"  Gain: {base - smooth:+.4f}")
    print()
    if base - smooth > 0.005:
        print("  Gain SURVIVES strict LOO. Real — adopt the smoothing.")
    elif base - smooth > 0:
        print("  Gain shrank under strict LOO to a marginal amount.")
        print("  Judgment call: likely not worth the added complexity.")
    else:
        print("  Gain DISAPPEARS under strict LOO — it was optimism, not signal.")
        print("  Do NOT adopt; ship the 0.837 correction-only engine.")


if __name__ == "__main__":
    main()
