"""
rubric_test.py
==============
A/B TEST: does giving the LLM your DETAILED component definitions (richer rubric)
reduce the scatter in its component scores, versus the current thin one-line
definitions?

WHY THIS SPECIFIC TEST
----------------------
The free error analysis showed ~97% of the LLM's gap is random scatter, not
systematic bias — so calibration-anchor prompting (which only fixes bias) won't
help. BUT the analysis couldn't rule out one thing: maybe some of that "scatter"
is the model MISUNDERSTANDING what a component means (e.g. scoring "Integration"
against a different definition than yours). If so, richer definitions could
genuinely reduce it. This test settles that.

THE COMPARISON (one variable changed: definition richness)
----------------------------------------------------------
  A) CURRENT prompt  : the scores already in llm_scores_cache.json (thin defs).
  B) RICHER prompt   : same everything, but with your detailed component
     definitions from RatingGuidelines added.
We re-research a held-out sample with prompt B, then compare each prompt's RAW
gap to your actual component scores. If B's gap is meaningfully lower, richer
definitions help and are worth adopting. If not, research is near its floor and
we rely on the correction layer instead.

HONESTY NOTE: we compare RAW (uncorrected) scores from both prompts against your
real scores. No correction layer involved — we're isolating the prompt's effect.

COST: re-researches SAMPLE_SIZE books (default 25) = a few cents.

HOW TO RUN (Thonny): press Run, confirm the spend. Needs apikey.txt.
"""

import json
import re
import numpy as np
import pandas as pd
import anthropic
import predict_engine as pe

MODEL = "claude-sonnet-4-5"
CACHE = "llm_scores_cache.json"
SAMPLE_SIZE = 25

LIVE = ["Plot", "Entertainment", "Action", "Ending", "Depth",
        "Emotional Impact", "Motivations", "Prose", "Narration",
        "Insights", "Thought-Provokingness", "Depth2", "Integration", "Originality"]

# --- The RICHER component definitions, drawn from your RatingGuidelines ---
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
    text = msg.content[0].text.strip()
    text = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()
    data = json.loads(text)
    data.pop("confidence", None)
    return {c: float(data[c]) for c in LIVE if c in data}


def main():
    books, gw, gcw = pe.load_everything()
    cache = json.load(open(CACHE))

    # Build the set of books that have BOTH your real scores and a cached score
    pairs = []
    for _, b in books.iterrows():
        if b["Book"] not in cache:
            continue
        s = cache[b["Book"]]["scores"]
        if all(c in s and not (isinstance(b[c], float) and np.isnan(b[c])) for c in LIVE):
            pairs.append(b)
    pairs = pd.DataFrame(pairs)

    # Stratified-ish sample for the test
    sample = pairs.sample(min(SAMPLE_SIZE, len(pairs)), random_state=42)
    print("=" * 60)
    print("RICHER-RUBRIC A/B TEST")
    print("=" * 60)
    print(f"Re-researching {len(sample)} books with the richer prompt and")
    print("comparing both prompts' RAW gap to your actual scores.\n")
    go = input(f"This makes {len(sample)} API calls (a few cents). Proceed? (y/n): ")
    if go.strip().lower() != "y":
        print("Skipped.")
        return

    client = anthropic.Anthropic(api_key=load_key())

    cur_errs, rich_errs = [], []
    cur_round, rich_round = 0, 0   # count .0/.5 values, to see if decimals improved
    print("\nResearching...\n")
    for _, b in sample.iterrows():
        title = b["Book"]
        cur = cache[title]["scores"]
        try:
            rich = research_rich(client, title, b["Author"], b["Genre"])
        except Exception as e:
            print(f"  {title[:30]}: ERROR {e}")
            continue
        for c in LIVE:
            you = b[c]
            cur_errs.append(abs(cur[c] - you))
            rich_errs.append(abs(rich[c] - you))
            if (cur[c] * 2) % 1 == 0:
                cur_round += 1
            if (rich[c] * 2) % 1 == 0:
                rich_round += 1
        print(f"  {title[:34]:<34} done")

    cur_mae = np.mean(cur_errs)
    rich_mae = np.mean(rich_errs)
    n = len(cur_errs)
    print("\n" + "=" * 60)
    print("RESULT  (raw gap to your actual component scores)")
    print("=" * 60)
    print(f"  Current prompt (thin defs) MAE : {cur_mae:.4f}")
    print(f"  Richer prompt (detailed)   MAE : {rich_mae:.4f}")
    print(f"  Change: {cur_mae - rich_mae:+.4f} "
          f"({(cur_mae-rich_mae)/cur_mae*100:+.1f}%)")
    print()
    print(f"  Scores landing on .0/.5 (of {n}):")
    print(f"    current prompt: {cur_round} ({cur_round/n*100:.0f}%)")
    print(f"    richer prompt : {rich_round} ({rich_round/n*100:.0f}%)")
    print()
    if rich_mae < cur_mae - 0.03:
        print("  Richer rubric MEANINGFULLY helps — worth adopting the new prompt.")
    elif rich_mae < cur_mae:
        print("  Richer rubric helps only marginally — probably not worth the")
        print("  added prompt complexity; research is near its floor.")
    else:
        print("  Richer rubric does NOT help. Research is at its floor; rely on")
        print("  the correction layer for accuracy.")


if __name__ == "__main__":
    main()
