"""
research_layer.py
=================
THE RESEARCH LAYER (Option A: LLM-as-researcher).

WHAT THIS IS
------------
The earlier engine ESTIMATES a book's 14 components by averaging similar books
you've read. Validation showed that's the accuracy bottleneck (MAE ~0.76 vs a
math-layer ceiling of ~0.08). This module replaces that guess with a GROUNDED
estimate: it asks an LLM to assess a specific book against YOUR actual rubric
(your 0-10 scale, your qualitative->numeric anchors) and return the 14
component scores. Those scores then flow through the SAME validated math
(components -> category averages -> regression -> WA).

THE DESIGN: A SWAPPABLE "RESEARCHER"
------------------------------------
`research_components(title, author, genre)` is the interface. Today it's backed
by the Anthropic API (`LLMResearcher`). Tomorrow you could back it by a reviews
database or a different model WITHOUT changing the validation harness -- the
harness only cares that a researcher returns 14 numbers.

THE HONESTY TEST (the whole point)
----------------------------------
`evaluate_researcher()` takes books you've ALREADY rated, asks the researcher
to score them WITHOUT seeing your scores, runs the result through the real
math, and compares predicted WA to your actual WA. If grounded research can't
beat the 0.76 analog baseline, it isn't adding value -- and you'll see that
honestly in the numbers.

COST: each book = one short API call, a fraction of a cent. The default test
set is a ~25-book stratified sample, so the whole run costs pennies.

SETUP
-----
1. apikey.txt in this folder (your sk-ant-... key, nothing else).
2. predict_engine.py + shrinkage_estimator.py in this folder.
3. `pip install anthropic` (Thonny: Tools -> Manage Packages -> anthropic).

HOW TO RUN (Thonny): press Run. It will (a) demo one book, then (b) ask before
spending money on the full sample evaluation.
"""

import json
import os
import re
import numpy as np
import pandas as pd

import anthropic
import predict_engine as pe


# ---------------------------------------------------------------------------
# Research-cache key normalization (latency: avoid false cache misses)
# ---------------------------------------------------------------------------
# Every research cache (llm_scores_richer.json, web_grounded_cache.json) is keyed
# by the bare book TITLE. A predicted/Discover-generated title that differs from a
# cached key only by case or whitespace ("gardens of the moon", "Gardens of the
# Moon ") was an exact-match MISS → a fresh ~6.8s (memory) / ~38–110s (web_search)
# LLM call for a book already in the cache. cache_lookup adds a normalized fallback
# so those hit. Deliberately GENTLE (case + whitespace only) — it does NOT strip
# subtitles or series suffixes, which could conflate genuinely different books.
def normalize_title(title):
    """Canonical cache key: trimmed, lowercased, internal whitespace collapsed.
    Empty string for a null/blank title. Pure; no dependencies."""
    return " ".join(str(title).strip().lower().split()) if title else ""


def cache_lookup(cache, title):
    """Look a title up in a title-keyed research cache. Tries the exact key first
    (the common fast path, O(1)), then falls back to a normalized match so a
    case/whitespace variant still hits. Returns the cache entry or None; never
    raises. The normalized scan is O(n) and runs ONLY on an exact miss — where the
    caller is about to make a multi-second LLM call anyway, so it is free by
    comparison. Callers keep their own entry validation (e.g. 'scores' present).

    Writes still store under the caller's raw title (no key migration), so every
    existing entry is preserved and no downstream reader that keys by the exact DB
    title is affected."""
    if title in cache:
        return cache[title]
    nt = normalize_title(title)
    if not nt:
        return None
    for k, v in cache.items():
        if normalize_title(k) == nt:
            return v
    return None


def _extract_json(text: str):
    """Parse JSON from LLM output robustly.

    Handles: clean JSON; ```json-fenced; JSON followed by prose or a stray
    fence; leading prose before the object; no-fence plain JSON.
    """
    text = text.strip()
    # Remove a matched open/close fence pair (not a global multiline sub).
    if text.startswith("```"):
        end = text.find("```", 3)
        if end != -1:
            text = text[text.index("\n", 0) + 1 : end].strip() if "\n" in text[:end] else text[3:end].strip()
    # Find the first JSON object or array and extract a balanced span.
    start = -1
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            break
    if start == -1:
        raise ValueError(f"No JSON object/array found in LLM output: {text[:200]!r}")
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"JSON parse error: {exc}. Raw (first 200): {text[:200]!r}"
                    ) from exc
    raise ValueError(f"Unbalanced JSON in LLM output: {text[:200]!r}")


MODEL = "claude-opus-4-8"            # Opus for grounded research scoring
WORKBOOK = pe.WORKBOOK


# ---------------------------------------------------------------------------
# Load the key from file (never hardcoded, never printed)
# ---------------------------------------------------------------------------
def load_key(path="apikey.txt"):
    """Return the Anthropic API key.

    Canonical source is the local, gitignored key file (apikey.txt) — the file
    wins whenever it exists and is non-empty. Deploy fallback: if the file is
    absent, use the ANTHROPIC_API_KEY environment variable, so hosted
    environments (e.g. Railway) that can't ship apikey.txt still serve the LLM
    endpoints. Raises FileNotFoundError when neither source has a key, so callers
    keep returning their existing 503 (key-missing) response.
    """
    try:
        with open(path) as f:
            key = f.read().strip()
        if key:
            return key
    except FileNotFoundError:
        pass
    env_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if env_key:
        return env_key
    raise FileNotFoundError(
        f"No Anthropic API key: {path} not found (or empty) and "
        f"ANTHROPIC_API_KEY is not set.")


def is_out_of_credits(exc) -> bool:
    """True when `exc` is the Anthropic "credit balance is too low" 400 — a
    terminal billing failure: every further call fails identically until the
    account is topped up (Console -> Plans & Billing). Matched on the stable
    error text (not the SDK class) so it survives client-library churn and holds
    even if the error is wrapped/re-raised. Callers turn this into a clear,
    actionable message instead of a generic 500 (backend) or an aborted batch
    (offline tooling)."""
    return "credit balance is too low" in str(exc).lower()


# ---------------------------------------------------------------------------
# Your rubric, baked into the prompt so the LLM scores in YOUR framework.
# (Pulled from RatingGuidelines Section 1 scale + Section 5B anchors.)
# ---------------------------------------------------------------------------
RUBRIC = """You are scoring a book on a 0-10 scale for a reader with a specific, consistent taste. Score each component INDEPENDENTLY against this scale:

10 = Transcendent. All-time best in this component.
9  = Exceptional. Among the strongest examples in its genre.
8  = Strong. Clearly above average; a genuine selling point.
7  = Good. Competent and enjoyable but unremarkable.
6  = Acceptable. Doesn't detract but unremarkable.
5  = Mediocre. A noticeable weakness.
<=4 = Poor. Actively bad; hurts the book.

Map reader-sentiment to numbers like this:
 "best in genre / blew me away" -> 9.0-9.5
 "one of my favorites / would re-read" -> 8.5-9.0
 "really strong / recommend it" -> 8.0-8.5
 "good, enjoyed it" -> 7.0-8.0
 "fine / didn't grab me" -> 6.0-7.0
 "disappointing / weak" -> 5.0-6.0
 "bad / DNF" -> <=4.0

Score each component on its own merits; a book can be 9-Plot and 5-Prose. Base your scores on what is actually known and widely reported about THIS specific book from reader communities and reviews -- not on the author's reputation in general. If you are genuinely uncertain about a component for this book, reflect that by scoring nearer the middle and noting low confidence."""

COMPONENT_DEFS = {
    "Plot": "Story structure, plotting, how events connect and build.",
    "Entertainment": "Sheer enjoyment / page-turner quality.",
    "Action": "Quality and impact of action/tension setpieces.",
    "Ending": "How well the ending pays off setup and lands.",
    "Depth": "Character depth and interiority.",
    "Emotional Impact": "Emotional resonance of the characters/story.",
    "Motivations": "Believability and richness of character motivations.",
    "Prose": "Sentence-level writing quality.",
    "Narration": "Narrative voice / POV handling.",
    "Insights": "Quality of ideas and observations.",
    "Thought-Provokingness": "How much it makes the reader think.",
    "Depth2": "Worldbuilding depth/lore richness (if applicable).",
    "Integration": "How naturally worldbuilding serves plot/character.",
    "Originality": "Novelty of the setting/world/ideas.",
}


# ---------------------------------------------------------------------------
# The researcher interface + the LLM implementation
# ---------------------------------------------------------------------------
class LLMResearcher:
    def __init__(self, components, model=MODEL, key_path="apikey.txt"):
        self.components = components
        self.model = model
        self.client = anthropic.Anthropic(api_key=load_key(key_path))

    def research(self, title, author, genre):
        """Return {component: score} for this book, scored in the rubric."""
        comp_lines = "\n".join(
            f'  "{c}": {COMPONENT_DEFS.get(c, c)}' for c in self.components)
        prompt = f"""{RUBRIC}

BOOK: "{title}" by {author}   (genre: {genre})

Score ONLY these {len(self.components)} components:
{comp_lines}

Respond with ONLY a JSON object mapping each component name to a number 0-10,
plus a "confidence" key ("high", "medium", or "low") for how well-known this
book is to you. No prose, no markdown, just the JSON. Example shape:
{{"Plot": 7.5, "Depth": 8.0, ..., "confidence": "medium"}}"""

        msg = self.client.messages.create(
            model=self.model, max_tokens=400,
            messages=[{"role": "user", "content": prompt}])
        text = msg.content[0].text.strip()
        data = _extract_json(text)
        confidence = data.pop("confidence", "unknown")
        scores = {c: float(data[c]) for c in self.components if c in data}
        return scores, confidence


# ---------------------------------------------------------------------------
# Turn researched components into a predicted WA via the validated math
# ---------------------------------------------------------------------------
def researched_wa(scores, genre, gcw, coeffs, ginfo):
    wcats = pe.components_to_wcats(scores, genre, gcw)
    wa_model = pe.regression_wa(coeffs, wcats["Story"], wcats["Character"],
                                wcats["Aesthetics"], wcats["Theme"])
    bias = ginfo.get(genre, {"bias": 0.0})["bias"]
    return wa_model + bias, wcats


# ---------------------------------------------------------------------------
# Stratified sample of already-rated books for the honesty test
# ---------------------------------------------------------------------------
def stratified_sample(books, n_per_genre=3, seed=42):
    rng = np.random.default_rng(seed)
    picks = []
    for g, sub in books.groupby("Genre"):
        k = min(n_per_genre, len(sub))
        picks.extend(sub.sample(k, random_state=int(rng.integers(1e6))).index.tolist())
    return books.loc[picks]


# ---------------------------------------------------------------------------
# The evaluation: does grounded research beat the analog baseline?
# ---------------------------------------------------------------------------
def evaluate_researcher(books, gw, gcw, researcher, sample, verbose=True):
    coeffs, r2, resid_sd = pe.fit_regression(books)
    ginfo = pe.genre_bias_and_trust(books, coeffs)

    rows = []
    for idx, b in sample.iterrows():
        try:
            scores, conf = researcher.research(b["Book"], b["Author"], b["Genre"])
            wa_pred, _ = researched_wa(scores, b["Genre"], gcw, coeffs, ginfo)
            rows.append({"Book": b["Book"], "Genre": b["Genre"],
                         "actual": b["WA"], "pred": wa_pred,
                         "err": abs(wa_pred - b["WA"]), "conf": conf})
            if verbose:
                print(f"  {b['Book'][:30]:<30} actual={b['WA']:.2f} "
                      f"researched={wa_pred:.2f}  miss={abs(wa_pred-b['WA']):.2f} "
                      f"[{conf}]")
        except Exception as e:
            print(f"  {b['Book'][:30]:<30} ERROR: {e}")
    return pd.DataFrame(rows)


def main():
    books, gw, gcw = pe.load_everything(WORKBOOK)
    comps = pe.components_of(books)
    researcher = LLMResearcher(comps)

    print("=" * 64)
    print("RESEARCH LAYER — LLM-as-researcher")
    print("=" * 64)

    # --- (a) one-book demo so you see it work before spending on a batch ---
    print("\nDEMO: researching one book (The Republic of Thieves)...")
    scores, conf = researcher.research("The Republic of Thieves",
                                       "Scott Lynch", "Epic Fantasy")
    print(f"  confidence: {conf}")
    print("  scores:", {k: round(v, 1) for k, v in scores.items()})
    coeffs, r2, resid_sd = pe.fit_regression(books)
    ginfo = pe.genre_bias_and_trust(books, coeffs)
    wa, _ = researched_wa(scores, "Epic Fantasy", gcw, coeffs, ginfo)
    print(f"  -> researched WA: {wa:.2f}")

    # --- (b) the honesty test, gated behind a confirmation ---
    sample = stratified_sample(books, n_per_genre=3)
    print(f"\nThe full honesty test will research {len(sample)} of your rated "
          f"books\n(one API call each, a few cents total) and compare to your "
          f"actual scores.")
    go = input("Run it now? (y/n): ").strip().lower()
    if go != "y":
        print("Skipped. Re-run and choose y when ready.")
        return

    print("\nResearching... (this takes a minute)\n")
    res = evaluate_researcher(books, gw, gcw, researcher, sample)

    if len(res):
        mae = res["err"].mean()
        print("\n" + "=" * 64)
        print("RESULT")
        print("=" * 64)
        print(f"  Research-layer MAE : {mae:.3f}   (n={len(res)})")
        print(f"  Shrinkage baseline : 0.760  (analog estimate)")
        print(f"  Naive baseline     : 0.914")
        if mae < 0.760:
            print(f"\n  *** Research BEATS the analog baseline by "
                  f"{0.760-mae:.3f}. The grounded layer adds real value. ***")
        else:
            print(f"\n  Research does NOT yet beat analogs (by {mae-0.760:.3f}).")
            print("  That's an honest finding -- see notes on improving the prompt.")
        # Accuracy by stated confidence -- does the model know when it knows?
        print("\n  MAE by the model's self-reported confidence:")
        for c, sub in res.groupby("conf"):
            print(f"    {c:<8} n={len(sub):<3} MAE={sub['err'].mean():.3f}")


if __name__ == "__main__":
    main()
