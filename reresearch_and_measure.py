"""
reresearch_and_measure.py
=========================
Two phases, run once:

PHASE 1 — Re-research your rated books with the RICHER prompt (the one the A/B
test showed cuts the raw gap ~6.7% and removes the .0/.5 quantization). Results
are cached to a NEW file (llm_scores_richer.json) so your original cache is
preserved and this is resumable / never double-charges.

PHASE 2 — Re-run the component-correction ladder on the RICHER scores, to find
the COMBINED floor: richer prompt + correction together. Compares against the
old-prompt numbers so we see whether the two improvements stack.

This answers: when we build the app, what's the real best-achievable component
accuracy — and which correction method to ship on top of the richer prompt.

COST: re-researches your rated books not already in llm_scores_richer.json
(~104-125 calls, a couple dollars), once. Resumable: re-running continues from
the cache. Phase 2 is free.

HOW TO RUN (Thonny): press Run, confirm the spend. Needs apikey.txt.
After it finishes, llm_scores_richer.json is your upgraded cache.
"""

import json
import os
import numpy as np
import pandas as pd
import anthropic
import predict_engine as pe
import research_layer as rl

MODEL = "claude-opus-4-8"
RICH_CACHE = "llm_scores_richer.json"

LIVE = ["Plot", "Entertainment", "Action", "Ending", "Depth",
        "Emotional Impact", "Motivations", "Prose", "Narration",
        "Insights", "Thought-Provokingness", "Depth2", "Integration", "Originality"]

K_GENRE = 6.0
# De-compression recalibration (2026-07-05), LOO-gated on the 127 rated books
# (faithful hybrid pipeline: grounded raw -> corr-smooth -> author_genre -> WA).
# The author_genre correction was over-shrunk, producing regression-to-the-mean:
# LOO bias (actual-predicted) was +0.65 WA in the top tier (favourites predicted
# too LOW), -0.84 in the bottom tier (weak books too HIGH), with the middle spot
# on. De-compressing (K_AUTHOR 4->2->0.5, plus SLOPE_LIFT below) shrinks the bias
# at BOTH tails together and lowers overall LOO WA MAE 0.600 -> 0.581 (top-tier
# MAE 0.70 -> 0.64, bottom 1.07 -> 0.99, middle flat, ceiling 9.50 -> 9.65).
# K_AUTHOR=0.5 trusts a single same-author book at ~0.67 weight -- aggressive, but
# LOO-validated for this taste-consistent reader (chosen 2026-07-05 over the safer
# K_AUTHOR=1). The residual top bias (+0.53) is irreducible: a new book can't be
# known to exceed its author baseline before it's read (the interval carries it).
# Two constants, fully reversible. K_GENRE stays 6.0 (lowering it hurt).
K_AUTHOR = 0.5

# SLOPE_LIFT blends the fitted per-genre regression (you ~ a + b*llm, whose slope
# b<1 makes it compressive) toward the slope-1 deviation model (llm + genre_dev),
# by this fraction, BEFORE the genre/author shrinkage. 0.0 = the original fitted
# line (full regression-to-the-mean); 1.0 = pure deviation (no slope compression).
# 0.5 was chosen with K_AUTHOR above in the 2026-07-05 LOO gate: it de-compresses
# the genre tail (bottom over-prediction -0.84 -> -0.65) at no accuracy cost.
# Applied identically in the genre_reg and author_genre correction ladders.
SLOPE_LIFT = 0.5

# Worldbuilding components: realist-genre books store WB actuals as the 0.0
# "no worldbuilding" sentinel (CLAUDE.md), not NULL, while the LLM still scores
# them normally. Rows where all three WB actuals are exactly 0 are excluded
# from the training pool (global/genre/author deviation) for these three
# components only — they're spurious ~8-pt errors, not real signal. This does
# NOT change which books get predicted, only what feeds the correction stats.
WB = ["Depth2", "Integration", "Originality"]

# Reuse the exact richer prompt from the A/B test.
RICH_DEFS = {
    "Plot": "Story structure and plotting — how events connect, build, and pay off. One of your three strongest predictors of overall rating; score it carefully.",
    "Entertainment": "Sheer page-turner enjoyment, independent of literary merit.",
    "Action": "Quality and impact of action/tension setpieces.",
    "Ending": "How well the ending pays off the book's setup. A strong predictor for you; endings that land lift the whole book.",
    "Depth": "Character depth and interiority — psychological richness. One of your three strongest predictors.",
    "Emotional Impact": "How emotionally resonant the characters and story are.",
    "Motivations": "Believability and richness of character motivations — why characters do what they do.",
    "Prose": "Sentence-level writing quality and craft.",
    "Narration": "Narrative voice and POV handling.",
    "Insights": "Quality and depth of the book's ideas and observations.",
    "Thought-Provokingness": "How much the book makes you think — lingering questions, reframing.",
    "Depth2": "Worldbuilding depth — lore richness and texture of the setting. Correlates strongly with your overall rating.",
    "Integration": "How naturally the worldbuilding serves plot and character (rather than sitting inert as info-dump).",
    "Originality": "Novelty of the setting/system. Note: a derivative-but-coherent world often scores fine — coherence matters more than novelty.",
}
ANCHORS = """Convert reader sentiment to numbers using these anchors:
 "best in genre / blew me away" -> 9.0-9.5
 "one of my favorites / would re-read" -> 8.5-9.0
 "really strong / recommend it" -> 8.0-8.5
 "good, enjoyed it" -> 7.0-8.0
 "fine / didn't grab me" -> 6.0-7.0
 "disappointing / weak" -> 5.0-6.0
 "bad / DNF" -> <=4.0
Score each component INDEPENDENTLY against the scale — a book can be 9-Plot and
5-Prose; do not smear one component toward another. Base scores on what is
actually reported about THIS specific book by reader communities, not the
author's general reputation. Use decimals freely (e.g. 7.3, 8.1) — do NOT round
to halves; give your genuine best estimate of the precise value."""


def load_key():
    with open("apikey.txt") as f:
        return f.read().strip()


def rich_prompt(title, author, genre):
    defs = "\n".join(f'  "{c}": {RICH_DEFS[c]}' for c in LIVE)
    return f"""You are scoring a book on a 0-10 scale for a specific reader with consistent, well-defined taste.

{ANCHORS}

Detailed definitions of each component (score against THESE meanings precisely):
{defs}

BOOK: "{title}" by {author}  (genre: {genre})

Respond with ONLY a JSON object mapping each of these {len(LIVE)} components to a
number 0-10 (decimals encouraged), plus a "confidence" key (high/medium/low):
{LIVE}
No prose, no markdown — just the JSON."""


def research_rich(client, title, author, genre):
    msg = client.messages.create(
        model=MODEL, max_tokens=400,
        messages=[{"role": "user", "content": rich_prompt(title, author, genre)}])
    data = rl._extract_json(msg.content[0].text)
    conf = data.pop("confidence", "unknown")
    return {c: float(data[c]) for c in LIVE if c in data}, conf


# ---------------------------------------------------------------------------
# PHASE 1: re-research with caching
# ---------------------------------------------------------------------------
def phase1_research(books):
    cache = {}
    if os.path.exists(RICH_CACHE):
        with open(RICH_CACHE) as f:
            cache = json.load(f)
    todo = [b for _, b in books.iterrows() if b["Book"] not in cache]
    print(f"Phase 1: {len(cache)} already cached, {len(todo)} to research.")
    if todo:
        go = input(f"  Research {len(todo)} books with the richer prompt (~${len(todo)*0.01:.2f})? (y/n): ")
        if go.strip().lower() != "y":
            print("  Skipped Phase 1 — Phase 2 will run on whatever is cached.")
            return cache
    client = anthropic.Anthropic(api_key=load_key())
    n = 0
    for b in todo:
        try:
            scores, conf = research_rich(client, b["Book"], b["Author"], b["Genre"])
            cache[b["Book"]] = {"scores": scores, "conf": conf}
            n += 1
            if n % 10 == 0:
                print(f"    ...{n} researched")
                with open(RICH_CACHE, "w") as f:
                    json.dump(cache, f, indent=2)
        except Exception as e:
            print(f"    {b['Book'][:30]}: ERROR {e}")
    with open(RICH_CACHE, "w") as f:
        json.dump(cache, f, indent=2)
    print(f"  Done. {RICH_CACHE} now has {len(cache)} books.\n")
    return cache


# ---------------------------------------------------------------------------
# PHASE 2: correction ladder on the richer scores
# (same methods as component_correction_test.py)
# ---------------------------------------------------------------------------
def build_pairs(books, cache):
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
                ok = False
                break
            rec["you_" + c] = float(yv)
            rec["llm_" + c] = float(lv)
        if ok:
            rows.append(rec)
    return pd.DataFrame(rows).reset_index(drop=True)


def fit_line(x, y):
    if len(x) < 3 or np.std(x) < 1e-9:
        return None
    b, a = np.polyfit(x, y, 1)
    return a, b


def correct_book(df, i, method):
    train = df.drop(df.index[i])
    train_no_sentinel = train[~((train["you_Depth2"] == 0)
                                 & (train["you_Integration"] == 0)
                                 & (train["you_Originality"] == 0))]
    b = df.loc[df.index[i]]
    out = {}
    for c in LIVE:
        llm = b["llm_" + c]
        pool = train_no_sentinel if c in WB else train
        dev_all = (pool["you_" + c] - pool["llm_" + c])
        global_dev = dev_all.mean()
        if method == "raw":
            out[c] = llm
        elif method == "genre_reg":
            g = pool[pool["Genre"] == b["Genre"]]
            n_g = len(g)
            line = fit_line(g["llm_" + c].values, g["you_" + c].values) if n_g >= 3 else None
            gdev = (g["you_" + c] - g["llm_" + c]).mean() if n_g else global_dev
            # SLOPE_LIFT de-compresses the fitted genre line (slope<1) toward the
            # slope-1 deviation model (llm + gdev), cutting regression-to-the-mean.
            gpred = ((1 - SLOPE_LIFT) * (line[0] + line[1] * llm)
                     + SLOPE_LIFT * (llm + gdev)) if line else llm + gdev
            global_pred = llm + global_dev
            w = n_g / (n_g + K_GENRE)
            out[c] = w * gpred + (1 - w) * global_pred
        elif method == "author_genre":
            g = pool[pool["Genre"] == b["Genre"]]
            n_g = len(g)
            line = fit_line(g["llm_" + c].values, g["you_" + c].values) if n_g >= 3 else None
            gdev = (g["you_" + c] - g["llm_" + c]).mean() if n_g else global_dev
            # SLOPE_LIFT de-compresses the fitted genre line (slope<1) toward the
            # slope-1 deviation model (llm + gdev), cutting regression-to-the-mean.
            gpred = ((1 - SLOPE_LIFT) * (line[0] + line[1] * llm)
                     + SLOPE_LIFT * (llm + gdev)) if line else llm + gdev
            global_pred = llm + global_dev
            wg = n_g / (n_g + K_GENRE)
            genre_pred = wg * gpred + (1 - wg) * global_pred
            au = pool[pool["Author"] == b["Author"]]
            n_a = len(au)
            if n_a > 0:
                adev = (au["you_" + c] - au["llm_" + c]).mean()
                wa = n_a / (n_a + K_AUTHOR)
                out[c] = wa * (llm + adev) + (1 - wa) * genre_pred
            else:
                out[c] = genre_pred
    return out


def evaluate(df, method):
    errs = []
    for i in range(len(df)):
        pred = correct_book(df, i, method)
        b = df.iloc[i]
        for c in LIVE:
            errs.append(abs(pred[c] - b["you_" + c]))
    return np.mean(errs)


def main():
    books, gw, gcw = pe.load_everything()
    cache = phase1_research(books)

    df = build_pairs(books, cache)
    print("=" * 60)
    print(f"PHASE 2: CORRECTION LADDER ON RICHER SCORES (n={len(df)})")
    print("=" * 60)
    raw = evaluate(df, "raw")
    greg = evaluate(df, "genre_reg")
    ag = evaluate(df, "author_genre")
    print(f"  Raw richer LLM                 : {raw:.4f}")
    print(f"  + Genre regression             : {greg:.4f}")
    print(f"  + Author level (full method)   : {ag:.4f}")
    print()
    print("  For comparison, on the OLD (thin-prompt) scores we measured:")
    print("    raw 1.053  ->  full correction 0.882")
    print()
    print("=" * 60)
    print("COMBINED FLOOR")
    print("=" * 60)
    print(f"  Richer prompt + full correction: {ag:.4f}")
    if ag < 0.882:
        print(f"  -> Beats the old combined floor (0.882) by {0.882-ag:.4f}.")
        print("     The improvements STACK. Ship richer prompt + correction.")
    else:
        print(f"  -> Does NOT beat old floor ({ag:.4f} vs 0.882) — the richer")
        print("     prompt and correction overlap (fixing the same gap). The")
        print("     richer prompt still wins on its own (decimals + raw accuracy);")
        print("     correction adds less on top of it.")


if __name__ == "__main__":
    main()
