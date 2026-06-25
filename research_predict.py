"""
research_predict.py
===================
App-facing glue for the research layer on the Predict page. It WIRES TOGETHER
existing pieces — it does not change any prediction math:

  * the LLM researcher                 (research_layer.LLMResearcher)
  * the validated roll-up math         (predict_engine / research_layer.researched_wa)
  * the per-genre taste correction     (the idea proven in personalize.py)

and exposes the two operations the website needs:

  research_book(...)  one cached API call -> a book's 14 component scores.
  blend(...)          combine the research-based WA with the instant analog WA
                      using an AUTOMATIC weight that depends on how much analog
                      data exists for that book's author, applying per-genre
                      taste correction only for well-sampled genres.
  list_series(...)    one API call -> the books of a named series in reading
                      order (each tagged with one of your genres) + a
                      completeness flag, so the UI can confirm before spending.

CACHING: reuses llm_scores_cache.json — the SAME file and shape used by
personalize.py / confirm_full.py:  { title: {"scores": {...}, "conf": "..."} }.
A book is therefore never researched twice across the whole project.
"""

import os
import re
import json
import math

import numpy as np

import predict_engine as pe
import research_layer as rl

CACHE = "llm_scores_cache.json"

WELL_SAMPLED_GENRE = 5      # min rated books in a genre to trust its taste correction
BLEND_TAPER = 2.0           # how fast research weight falls off as analogs accrue
RESEARCH_FLOOR = 0.4        # research weight when you have many analogs by the author


# ---------------------------------------------------------------------------
# Cache (shared with personalize.py / confirm_full.py)
# ---------------------------------------------------------------------------
def load_cache(path=CACHE):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_cache(cache, path=CACHE):
    with open(path, "w") as f:
        json.dump(cache, f, indent=2)


def get_researcher(comps, key_path="apikey.txt"):
    """Construct the LLM researcher (raises if the key/package is missing)."""
    return rl.LLMResearcher(comps, key_path=key_path)


# ---------------------------------------------------------------------------
# Automatic blend weight: research-heavy with no analogs, tapering toward
# RESEARCH_FLOOR as you've read more by this author.
#   n=0 -> 1.00,  n=1 -> 0.76,  n=2 -> 0.62,  n=4 -> 0.48,  n>=6 -> ~0.43
# ---------------------------------------------------------------------------
def research_weight(n_author):
    return RESEARCH_FLOOR + (1.0 - RESEARCH_FLOOR) * math.exp(-n_author / BLEND_TAPER)


def _raw_research_wa(scores, genre, gcw, coeffs, ginfo, comps):
    s = {c: float(scores[c]) for c in comps if c in scores}
    return rl.researched_wa(s, genre, gcw, coeffs, ginfo)   # (wa, wcats)


def genre_taste_correction(genre, books, cache, gcw, coeffs, ginfo, comps,
                           min_n=WELL_SAMPLED_GENRE):
    """
    Mean (your WA - raw research WA) over YOUR rated books in this genre that
    are already in the cache. Returns (correction, n_used, applied). Only
    applied when the genre is well-sampled (n_used >= min_n); otherwise 0.
    """
    devs = []
    for _, b in books[books["Genre"] == genre].iterrows():
        entry = cache.get(b["Book"])
        if not entry:
            continue
        raw, _ = _raw_research_wa(entry["scores"], genre, gcw, coeffs, ginfo, comps)
        devs.append(float(b["WA"]) - raw)
    n = len(devs)
    if n >= min_n:
        return float(np.mean(devs)), n, True
    return 0.0, n, False


def research_book(title, author, genre, researcher, cache):
    """Return (scores, conf, from_cache). On a miss, adds to `cache` in place."""
    if title in cache:
        e = cache[title]
        return e["scores"], e.get("conf", "?"), True
    scores, conf = researcher.research(title, author, genre)
    cache[title] = {"scores": scores, "conf": conf}
    return scores, conf, False


def blend(title, author, genre, scores, conf, analog_wa, resid_sd,
          books, gcw, coeffs, ginfo, cache, comps):
    """
    Combine the research-based WA with the analog WA. The research side gets a
    per-genre taste correction (only for well-sampled genres); the blend weight
    is set automatically from how many books by this author you've rated.
    Returns a display dict (no DB writes, no math changes).
    """
    raw_wa, wcats = _raw_research_wa(scores, genre, gcw, coeffs, ginfo, comps)
    corr, n_genre, taste_applied = genre_taste_correction(
        genre, books, cache, gcw, coeffs, ginfo, comps)
    research_wa = raw_wa + corr

    n_author = int((books["Author"] == author).sum())
    w = research_weight(n_author)
    blend_wa = w * research_wa + (1.0 - w) * analog_wa

    half = 1.645 * resid_sd
    ci = (blend_wa - half, blend_wa + half)
    rank = int((books["WA"] > blend_wa).sum() + 1)
    return {
        "title": title, "author": author, "genre": genre,
        "raw_research_wa": raw_wa, "research_wa": research_wa,
        "analog_wa": analog_wa, "blend_wa": blend_wa,
        "w_research": w, "n_author": n_author,
        "taste_applied": taste_applied, "taste_correction": corr,
        "n_genre": n_genre, "conf": conf,
        "ci": ci, "rank": rank, "total": len(books), "wcats": wcats,
    }


# ---------------------------------------------------------------------------
# Series listing: one API call, gated behind UI confirmation before research.
# ---------------------------------------------------------------------------
def list_series(series_name, allowed_genres, model=rl.MODEL, key_path="apikey.txt"):
    """
    Return (books, complete, note) where books is a list of
    {"title","author","genre"} in reading order, each genre chosen from
    `allowed_genres`, and `complete` flags the model's own certainty.
    """
    import anthropic
    client = anthropic.Anthropic(api_key=rl.load_key(key_path))
    genre_list = ", ".join(sorted(allowed_genres))
    prompt = f'''List the books in the book series "{series_name}" in reading order.
For each book give its title, the author, and the single best-fitting genre chosen
EXACTLY from this list (copy the spelling exactly):
{genre_list}

If you are not fully certain the list is complete or correct, reflect that.
Respond with ONLY a JSON object — no prose, no markdown:
{{"books": [{{"title": "...", "author": "...", "genre": "..."}}],
  "complete": true,
  "note": "short caveat if uncertain, else empty"}}'''
    msg = client.messages.create(
        model=model, max_tokens=1200,
        messages=[{"role": "user", "content": prompt}])
    text = msg.content[0].text.strip()
    text = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()
    data = json.loads(text)
    return (data.get("books", []),
            bool(data.get("complete", False)),
            data.get("note", ""))
