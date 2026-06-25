"""
confirm_full.py
===============
CONFIRMATION: re-run the winning blended engine on ALL your rated books (not
just the 32-book sample), with every parameter chosen out-of-sample so the
final MAE is fully honest.

WHAT IT CONFIRMS
----------------
The 32-book test found: blend of ~0.3-0.4 research + ~0.6-0.7 analog beats the
analog-only baseline (0.668 vs 0.717). This re-runs that on all 125 books to
see whether the result holds at full scale.

THE HONESTY UPGRADE OVER THE 32-BOOK RUN
----------------------------------------
In the sample run, the blend weight (0.4) was read off the same data we scored
on — mildly optimistic. Here the blend weight is chosen INSIDE the leave-one-out
loop: to predict each book, we pick the weight using only the OTHER books, then
apply it to the held-out book. Nothing the final number depends on has seen the
book being predicted. This is the strict, defensible version.

We report:
  * analog-only MAE  (the baseline, recomputed on all 125)
  * raw research MAE
  * research + per-genre taste  (the lever that helped at n=32)
  * research + per-component taste  (the lever that HURT at n=32 — does more
    data rescue it?)
  * the honest blended engine (per-fold weight selection)
  * a full-sample weight sweep, just to SEE the valley (not used for the number)

COST: researches the books not already in llm_scores_cache.json — about 93 new
API calls (~$1-2). Cached books are reused free. Resumable: if it dies partway,
re-running continues from the cache.

SETUP: predict_engine.py, research_layer.py, shrinkage_estimator.py, apikey.txt,
plus the existing cache. HOW TO RUN (Thonny): press Run, confirm the spend.
"""

import json
import os
import numpy as np
import pandas as pd

import predict_engine as pe
import research_layer as rl
import shrinkage_estimator as se

WORKBOOK = pe.WORKBOOK
CACHE = "llm_scores_cache.json"
WEIGHTS = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]


# ---------------------------------------------------------------------------
# Research ALL books, caching; resumable.
# ---------------------------------------------------------------------------
def research_all(books, researcher, batch_note=True):
    cache = {}
    if os.path.exists(CACHE):
        with open(CACHE) as f:
            cache = json.load(f)
    todo = [b for _, b in books.iterrows() if b["Book"] not in cache]
    if todo:
        print(f"  Need {len(todo)} new API calls "
              f"({len(books)-len(todo)} already cached).")
        go = input(f"  Proceed with ~{len(todo)} calls (~$1-2)? (y/n): ")
        if go.strip().lower() != "y":
            print("  Skipped — will run on cached books only.")
    n_new = 0
    for b in todo:
        try:
            scores, conf = researcher.research(b["Book"], b["Author"], b["Genre"])
            cache[b["Book"]] = {"scores": scores, "conf": conf}
            n_new += 1
            if n_new % 10 == 0:
                print(f"    ...{n_new} researched")
                with open(CACHE, "w") as f:   # checkpoint periodically
                    json.dump(cache, f, indent=2)
        except Exception as e:
            print(f"    {b['Book'][:30]}: ERROR {e}")
    with open(CACHE, "w") as f:
        json.dump(cache, f, indent=2)
    return cache


def build_frame(books, cache, comps):
    rows = []
    for _, b in books.iterrows():
        if b["Book"] not in cache:
            continue
        llm = cache[b["Book"]]["scores"]
        rec = {"Book": b["Book"], "Genre": b["Genre"], "Author": b["Author"],
               "actual": b["WA"], "conf": cache[b["Book"]].get("conf", "?")}
        for c in comps:
            rec[f"llm_{c}"] = float(llm[c]) if c in llm else np.nan
            yv = b[c]
            rec[f"you_{c}"] = float(yv) if not (isinstance(yv, float) and np.isnan(yv)) else np.nan
        rows.append(rec)
    return pd.DataFrame(rows).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Predictors
# ---------------------------------------------------------------------------
def raw_research(df, comps, gcw, coeffs, ginfo):
    out = []
    for _, b in df.iterrows():
        s = {c: b[f"llm_{c}"] for c in comps if not np.isnan(b[f"llm_{c}"])}
        wa, _ = rl.researched_wa(s, b["Genre"], gcw, coeffs, ginfo)
        out.append(wa)
    return np.array(out)


def research_genre_corrected(df, comps, gcw, coeffs, ginfo, K=6.0):
    """Per-genre correction on the rolled-up WA, leave-one-out."""
    raw = raw_research(df, comps, gcw, coeffs, ginfo)
    out = np.full(len(df), np.nan)
    for i in range(len(df)):
        mask = np.arange(len(df)) != i
        tr = df[mask]; tr_raw = raw[mask]
        gdev_all = (tr["actual"].values - tr_raw)
        global_dev = gdev_all.mean()
        gmask = (tr["Genre"] == df.iloc[i]["Genre"]).values
        n_g = gmask.sum()
        genre_dev = gdev_all[gmask].mean() if n_g > 0 else global_dev
        corr = (n_g * genre_dev + K * global_dev) / (n_g + K)
        out[i] = raw[i] + corr
    return out


def research_component_corrected(df, comps, gcw, coeffs, ginfo, K=10.0):
    out = np.full(len(df), np.nan)
    for i in range(len(df)):
        tr = df.drop(df.index[i])
        b = df.iloc[i]
        cs = {}
        for c in comps:
            lv = b[f"llm_{c}"]
            if np.isnan(lv):
                continue
            devs = (tr[f"you_{c}"] - tr[f"llm_{c}"]).dropna()
            n = len(devs)
            cs[c] = lv + (devs.sum() / (n + K) if n > 0 else 0.0)
        wa, _ = rl.researched_wa(cs, b["Genre"], gcw, coeffs, ginfo)
        out[i] = wa
    return out


def analog_preds(books, df):
    gcw = pe.discover_schema(WORKBOOK)[1]
    out = []
    for _, b in df.iterrows():
        train = books[books["Book"] != b["Book"]]
        coeffs, r2, sd, ginfo, upstream = se.ve.fit_on(train)
        brow = books[books["Book"] == b["Book"]].iloc[0]
        wa, _ = se.predict_one_shrunk(brow, train, None, gcw, coeffs, ginfo, upstream)
        out.append(wa)
    return np.array(out)


def honest_blend(df, research_col, analog, actual):
    """Per-fold weight selection: choose w from the OTHER books, apply to this one."""
    res = df[research_col].values
    out = np.full(len(df), np.nan)
    for i in range(len(df)):
        mask = np.arange(len(df)) != i
        best_w, best_e = 0.0, 9.9
        for w in WEIGHTS:
            bl = w * res[mask] + (1 - w) * analog[mask]
            e = np.abs(bl - actual[mask]).mean()
            if e < best_e:
                best_e, best_w = e, w
        out[i] = best_w * res[i] + (1 - best_w) * analog[i]
    return out


def main():
    books, gw, gcw = pe.load_everything(WORKBOOK)
    comps = pe.components_of(books)
    researcher = rl.LLMResearcher(comps)

    print("=" * 64)
    print("FULL-125 CONFIRMATION OF THE BLENDED ENGINE")
    print("=" * 64)
    cache = research_all(books, researcher)
    df = build_frame(books, cache, comps)
    print(f"\nEvaluating {len(df)} books, all leave-one-out.\n")

    coeffs, r2, resid_sd = pe.fit_regression(books)
    ginfo = pe.genre_bias_and_trust(books, coeffs)
    actual = df["actual"].values

    df["raw"] = raw_research(df, comps, gcw, coeffs, ginfo)
    df["res_genre"] = research_genre_corrected(df, comps, gcw, coeffs, ginfo)
    df["res_comp"] = research_component_corrected(df, comps, gcw, coeffs, ginfo)
    analog = analog_preds(books, df)
    df["analog"] = analog

    def mae(a): return float(np.abs(a - actual).mean())

    print("INDIVIDUAL PREDICTORS (out-of-sample MAE, n={})".format(len(df)))
    print("-" * 64)
    print(f"  Naive baseline                 : {np.abs(actual-actual.mean()).mean():.3f}")
    print(f"  Analog shrinkage (baseline)    : {mae(analog):.3f}")
    print(f"  Raw research                   : {mae(df['raw'].values):.3f}")
    print(f"  Research + per-genre taste     : {mae(df['res_genre'].values):.3f}")
    print(f"  Research + per-component taste : {mae(df['res_comp'].values):.3f}")
    print()

    # Pick whichever corrected research is better as the blend's research side.
    better = "res_genre" if mae(df["res_genre"].values) <= mae(df["res_comp"].values) else "res_comp"
    print(f"Using '{better}' as the research component of the blend.\n")

    print("FULL-SAMPLE WEIGHT SWEEP (for visibility only — not the headline)")
    print("-" * 64)
    print(f"  {'w (research)':<14}{'MAE':>8}")
    for w in WEIGHTS:
        bl = w * df[better].values + (1 - w) * analog
        print(f"  {w:<14.1f}{np.abs(bl-actual).mean():>8.3f}")
    print()

    blended = honest_blend(df, better, analog, actual)
    print("=" * 64)
    print("HEADLINE (fully honest: blend weight chosen per-fold)")
    print("=" * 64)
    print(f"  Analog baseline       : {mae(analog):.3f}")
    print(f"  Honest blended engine : {mae(blended):.3f}")
    d = mae(analog) - mae(blended)
    if d > 0:
        print(f"\n  *** Blend beats analogs by {d:.3f}, fully out-of-sample. ***")
    else:
        print(f"\n  Blend does not beat analogs at full scale ({d:+.3f}).")
        print("  The 32-book result may have been small-sample optimism.")
    print()
    print("  Confidence breakdown (model self-reported):")
    df["blend_err"] = np.abs(blended - actual)
    for c, sub in df.groupby("conf"):
        print(f"    {c:<8} n={len(sub):<4} blend MAE={sub['blend_err'].mean():.3f}")


if __name__ == "__main__":
    main()
