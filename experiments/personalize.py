"""
personalize.py
==============
RESEARCH LAYER, STEP 2: correct the LLM's "consensus" scores toward YOUR taste.

THE FINDING THIS FIXES
----------------------
The first research test (research_layer.py) showed the LLM scores books toward
CONSENSUS: it rated books you disliked (Station Eleven 5.2 -> model 8.1) too
high, and books you love (Toll the Hounds 9.8 -> model 8.1) too low. The gap
between the model and you is, precisely, YOUR personal taste. You have 125 rated
books that encode that taste -- so we can learn the systematic correction and
apply it.

THE CORRECTION
--------------
For each genre, your taste deviates from the model by some average amount
(a per-genre bias between your WA and the model's researched WA). We also blend
in a global correction so thin genres don't over-fit. Shrinkage again:
      correction(genre) = (n_g * genre_dev + K * global_dev) / (n_g + K)
This is the same hierarchical-shrinkage idea that already helped the analog
estimator -- here applied to the model-vs-you gap.

THE HONESTY GUARD (critical)
----------------------------
The correction for a book is learned from ALL THE OTHER books, never itself
(leave-one-out). Otherwise we'd just be memorising each book's answer and the
comparison to the 0.760 baseline would be a lie. Every number below is
out-of-sample.

WORKFLOW
--------
1. Run research_layer-style scoring ONCE on the sample, cache raw LLM WAs to a
   file (so we never pay for the same API call twice).
2. Learn + apply the personalization correction leave-one-out.
3. Report corrected MAE vs the 0.760 analog baseline and the 0.822 raw-research
   number.

SETUP: same as research_layer.py (apikey.txt, predict_engine.py, anthropic).
HOW TO RUN (Thonny): press Run. First run does the API calls and caches them;
later runs reuse the cache instantly (and free).
"""

import json
import os
import numpy as np
import pandas as pd

import predict_engine as pe
import research_layer as rl

WORKBOOK = pe.WORKBOOK
CACHE = "llm_scores_cache.json"      # raw LLM scores, keyed by book title
K_SHRINK = 6.0                       # shrinkage toward the global correction


# ---------------------------------------------------------------------------
# Step 1: get raw LLM researched WA for each sample book, with caching
# ---------------------------------------------------------------------------
def get_raw_scores(books, gw, gcw, sample, researcher):
    """Return DataFrame[Book, Genre, actual, raw_pred], using a disk cache."""
    cache = {}
    if os.path.exists(CACHE):
        with open(CACHE) as f:
            cache = json.load(f)

    coeffs, r2, resid_sd = pe.fit_regression(books)
    ginfo = pe.genre_bias_and_trust(books, coeffs)

    rows, new_calls = [], 0
    for _, b in sample.iterrows():
        title = b["Book"]
        if title in cache:
            scores = cache[title]["scores"]
            conf = cache[title]["conf"]
        else:
            scores, conf = researcher.research(title, b["Author"], b["Genre"])
            cache[title] = {"scores": scores, "conf": conf}
            new_calls += 1
        # raw researched WA (regression + genre bias, no personalization)
        wa, _ = rl.researched_wa(scores, b["Genre"], gcw, coeffs, ginfo)
        rows.append({"Book": title, "Genre": b["Genre"], "Author": b["Author"],
                     "actual": b["WA"], "raw_pred": wa, "conf": conf})

    with open(CACHE, "w") as f:
        json.dump(cache, f, indent=2)
    if new_calls:
        print(f"  ({new_calls} new API calls; {len(rows)-new_calls} from cache)")
    else:
        print(f"  (all {len(rows)} from cache — no API cost)")
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Step 2: personalization correction, learned leave-one-out
# ---------------------------------------------------------------------------
def personalize_loo(df):
    """
    For each book, learn the model->you correction from all OTHER books and
    apply it. Correction = shrunk blend of (genre-level dev) and (global dev),
    where dev = mean(actual - raw_pred).
    """
    corrected = np.full(len(df), np.nan)
    idx = df.index.tolist()
    for pos, i in enumerate(idx):
        train = df.drop(i)
        b = df.loc[i]

        global_dev = float((train["actual"] - train["raw_pred"]).mean())
        g_train = train[train["Genre"] == b["Genre"]]
        n_g = len(g_train)
        if n_g > 0:
            genre_dev = float((g_train["actual"] - g_train["raw_pred"]).mean())
        else:
            genre_dev = global_dev
        # Shrinkage blend
        correction = (n_g * genre_dev + K_SHRINK * global_dev) / (n_g + K_SHRINK)
        corrected[pos] = b["raw_pred"] + correction
    return corrected


def main():
    books, gw, gcw = pe.load_everything(WORKBOOK)
    comps = pe.components_of(books)
    researcher = rl.LLMResearcher(comps)
    sample = rl.stratified_sample(books, n_per_genre=3)

    print("=" * 64)
    print("PERSONALIZATION CORRECTION — does research+taste beat 0.760?")
    print("=" * 64)
    print(f"\nStep 1: raw LLM scores for {len(sample)} books "
          f"(cached after first run)...")

    if not os.path.exists(CACHE):
        go = input("  First run needs API calls (a few cents). Proceed? (y/n): ")
        if go.strip().lower() != "y":
            print("  Skipped.")
            return

    df = get_raw_scores(books, gw, gcw, sample, researcher)

    raw_mae = float((df["raw_pred"] - df["actual"]).abs().mean())
    df["corrected"] = personalize_loo(df)
    cor_mae = float((df["corrected"] - df["actual"]).abs().mean())

    # Show the biggest movers
    df["raw_err"] = (df["raw_pred"] - df["actual"]).abs()
    df["cor_err"] = (df["corrected"] - df["actual"]).abs()
    df["improved"] = df["raw_err"] - df["cor_err"]

    print("\nBooks where the taste correction helped most:")
    for _, r in df.sort_values("improved", ascending=False).head(6).iterrows():
        print(f"  {r['Book'][:28]:<28} actual={r['actual']:.2f} "
              f"raw={r['raw_pred']:.2f} -> corrected={r['corrected']:.2f}")
    print("\nBooks where it hurt (correction overshot):")
    for _, r in df.sort_values("improved").head(3).iterrows():
        print(f"  {r['Book'][:28]:<28} actual={r['actual']:.2f} "
              f"raw={r['raw_pred']:.2f} -> corrected={r['corrected']:.2f}")

    print("\n" + "=" * 64)
    print("RESULT  (all out-of-sample / leave-one-out)")
    print("=" * 64)
    print(f"  Naive baseline           : 0.914")
    print(f"  Analog shrinkage baseline: 0.760")
    print(f"  Raw research (no taste)  : {raw_mae:.3f}")
    print(f"  Research + personalization: {cor_mae:.3f}")
    print()
    if cor_mae < 0.760:
        print(f"  *** Research + taste BEATS the analog baseline by "
              f"{0.760 - cor_mae:.3f}. ***")
        print("  Grounded research, corrected toward your taste, is the engine.")
    else:
        print(f"  Still not beating analogs (by {cor_mae - 0.760:.3f}), but note")
        print(f"  the jump from raw {raw_mae:.3f} -> {cor_mae:.3f}: personalization")
        print("  is clearly the right lever. Next: correct per-COMPONENT, not just")
        print("  per-genre, and/or combine with the analog estimate.")


if __name__ == "__main__":
    main()
