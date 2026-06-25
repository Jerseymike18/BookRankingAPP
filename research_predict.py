"""
research_predict.py
===================
App-facing glue for the VALIDATED unfamiliar-book research path on the Predict
page. It WIRES TOGETHER the reference implementation in reresearch_and_measure.py
— it does NOT change the prediction math or the DB schema.

The validated method (leave-one-out vs your real component scores, component MAE
0.837 vs the old thin-prompt 1.05) has two parts that STACK:

  1. A RICHER research prompt with detailed component definitions + anchors
     (reresearch_and_measure.rich_prompt / RICH_DEFS / ANCHORS). Returns 14
     fine-grained component scores (decimals, not .0/.5) plus a confidence flag.
  2. An AUTHOR+GENRE hierarchical correction mapping the LLM's scores onto your
     scale (reresearch_and_measure.correct_book, method "author_genre"): genre
     regression where the data supports it with a deviation fallback when thin,
     blended with an author-level deviation, K_GENRE=6 / K_AUTHOR=4 shrinkage.

The CORRECTED components are what get displayed and stored. The WA is rolled up
from the corrected components via the SAME category-average math db_loader uses
for your rated books, so a researched book is internally consistent with them.

Operations the website needs:
  research_book(...)       one cached richer-prompt API call -> 14 scores + conf.
  correct_and_predict(...) apply the author+genre correction, roll up to a WA,
                           and report how well-grounded the correction was.
  list_series(...)         one API call -> the books of a named series in order.

CACHING: uses llm_scores_richer.json — the richer-prompt cache that already holds
your rated books (the LLM side of the correction training data). A book is never
re-researched; books you predict are added to it.
"""

import os
import re
import json

import numpy as np
import pandas as pd

import anthropic
import db_loader
import research_layer as rl
import reresearch_and_measure as rm

CACHE = rm.RICH_CACHE        # "llm_scores_richer.json"
LIVE = rm.LIVE               # canonical 14 components, reference order

WELL_SAMPLED_GENRE = 5       # genre below this is flagged as lower-reliability grounding
BLEND = 0.2                  # correlation-smoothing weight (validated winning variant)


# ---------------------------------------------------------------------------
# Richer-prompt cache (shared with reresearch_and_measure.py)
# ---------------------------------------------------------------------------
def load_cache(path=CACHE):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_cache(cache, path=CACHE):
    with open(path, "w") as f:
        json.dump(cache, f, indent=2)


def get_client(key_path="apikey.txt"):
    """Anthropic client for the richer-prompt researcher (raises if key missing)."""
    return anthropic.Anthropic(api_key=rl.load_key(key_path))


def _normalize_keywords(raw):
    """Coerce a model's keyword output into the EXACT comma-separated, lowercase
    string format the existing 447 entries use (so the Read Queue keyword filter,
    which does a case-insensitive substring match, keeps working). Accepts either
    a list or an already-joined string."""
    if raw is None:
        return ""
    if isinstance(raw, (list, tuple)):
        parts = [str(t) for t in raw]
    else:
        parts = str(raw).split(",")
    tags = [t.strip().lower() for t in parts if t and t.strip()]
    return ", ".join(tags)


# Appended to the validated richer prompt so the SAME single API call that scores
# the 14 components (+ confidence) also returns a blurb and keywords. The format
# instructions mirror the existing 447 entries so generated rows sit naturally
# alongside them.
_BLURB_KW_INSTRUCTIONS = """

ALSO include these two keys in the SAME JSON object:
  "blurb": one to two sentences capturing what makes THIS book distinctive — its
    hook, tone, and what kind of reader it suits. Concise and specific, not
    generic marketing copy. Example: "The series opener that defined modern
    grimdark political fantasy — seven POVs and a willingness to kill anyone."
  "keywords": a comma-separated string of about 7-10 lowercase descriptive tags
    mixing genre, tone, structure, and vibe. Examples:
    "epic fantasy, political, multi-POV, grimdark, scheming, medieval, morally-grey"
    "heist, dark humor, sharp prose, road journey, roguish, secondary world, voice-driven"
"""


def research_rich_plus(client, title, author, genre):
    """One richer-prompt API call that returns the 14 components, a confidence
    flag, a blurb, and keywords together. Returns (scores, conf, blurb, keywords).
    Reuses the validated rich prompt (reresearch_and_measure.rich_prompt) and only
    appends the blurb/keywords request — the component scoring is unchanged."""
    prompt = rm.rich_prompt(title, author, genre) + _BLURB_KW_INSTRUCTIONS
    msg = client.messages.create(
        model=rm.MODEL, max_tokens=700,
        messages=[{"role": "user", "content": prompt}])
    text = msg.content[0].text.strip()
    text = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()
    data = json.loads(text)
    conf = data.pop("confidence", "unknown")
    blurb = (data.pop("blurb", "") or "").strip()
    keywords = _normalize_keywords(data.pop("keywords", ""))
    scores = {c: float(data[c]) for c in LIVE if c in data}
    return scores, conf, blurb, keywords


def research_book(title, author, genre, client, cache):
    """Return (scores, conf, blurb, keywords, from_cache). On a miss, researches
    with the RICHER prompt (extended to also yield a blurb + keywords in the same
    call) and adds it to `cache` in place so the book is never re-researched.
    Cache entries written by the batch reference script may predate the blurb/
    keywords fields, so those default to empty strings."""
    if title in cache:
        e = cache[title]
        return (e["scores"], e.get("conf", "?"),
                e.get("blurb", ""), e.get("keywords", ""), True)
    scores, conf, blurb, keywords = research_rich_plus(client, title, author, genre)
    cache[title] = {"scores": scores, "conf": conf,
                    "blurb": blurb, "keywords": keywords}
    return scores, conf, blurb, keywords, False


def generate_blurb_keywords(title, author, genre, client, model=rm.MODEL):
    """Small standalone call that produces ONLY a blurb + keywords, for
    recommendations added without going through research. Returns (blurb,
    keywords); for obscure books the model may return a thin/empty result —
    that's acceptable, the caller handles it gracefully."""
    prompt = f'''Write a blurb and keywords for the book "{title}" by {author} (genre: {genre}).

"blurb": one to two sentences capturing what makes THIS book distinctive — its
hook, tone, and what kind of reader it suits. Concise and specific, not generic
marketing copy. Example: "The series opener that defined modern grimdark
political fantasy — seven POVs and a willingness to kill anyone."
"keywords": a comma-separated string of about 7-10 lowercase descriptive tags
mixing genre, tone, structure, and vibe. Example:
"epic fantasy, political, multi-POV, grimdark, scheming, medieval, morally-grey"

If this book is obscure and you are uncertain, give your best brief guess rather
than refusing. Respond with ONLY a JSON object — no prose, no markdown:
{{"blurb": "...", "keywords": "..."}}'''
    msg = client.messages.create(
        model=model, max_tokens=400,
        messages=[{"role": "user", "content": prompt}])
    text = msg.content[0].text.strip()
    text = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()
    data = json.loads(text)
    blurb = (data.get("blurb", "") or "").strip()
    keywords = _normalize_keywords(data.get("keywords", ""))
    return blurb, keywords


# ---------------------------------------------------------------------------
# WA roll-up from components — identical to db_loader (rated books) and to
# app.load_recommendations (the mood queue), so a researched book's WA is
# computed the same way as everything else.
# ---------------------------------------------------------------------------
def _wa_from_components(scores, genre, gw, gcw):
    wa = 0.0
    for cat in db_loader.CATEGORY_OF_INTEREST:
        wcat = db_loader._weighted_cat_avg(scores, genre, cat, gcw)
        wa += wcat * (gw.get(genre, {}).get(cat, 0) or 0)
    return wa


# ---------------------------------------------------------------------------
# Correlation smoothing — a validated PREPROCESSING step that runs BEFORE the
# author+genre correction. It exploits the strong intercorrelation among your
# component scores: each component is predicted from the LLM scores of the OTHER
# 13 (a regression learned on your rated books), and the raw value is nudged 20%
# toward that implied value. Leave-one-out validated: component MAE 0.837 ->
# ~0.827. Reference implementation: correlation_verify.corr_models / main.
# The models are stable, so they're built ONCE at load (see app.get_corr_models)
# rather than refit per prediction.
# ---------------------------------------------------------------------------
def build_corr_models(books, cache):
    """Fit your_score_c ~ LLM(other 13 components) on the rated-book pairs.
    Returns {component: (others, coef)}. Reference: correlation_verify.corr_models.
    Trained on ALL rated books (stable), so build once at load."""
    train = rm.build_pairs(books, cache)
    models = {}
    for c in LIVE:
        others = [o for o in LIVE if o != c]
        X = np.column_stack(
            [np.ones(len(train))] + [train["llm_" + o].values for o in others])
        coef, *_ = np.linalg.lstsq(X, train["you_" + c].values, rcond=None)
        models[c] = (others, coef)
    return models


def smooth_components(scores, models, blend=BLEND):
    """Correlation-smooth raw LLM components: for each component predict its value
    from the OTHER 13 raw LLM scores and blend toward it
    (smoothed = blend*implied + (1-blend)*raw). Returns a new dict; the implied
    value for every component is computed from the ORIGINAL raw vector, exactly as
    the reference (correlation_verify.main). If any component is missing the input
    is returned unchanged (smoothing needs the full vector)."""
    if models is None or not all(o in scores for o in LIVE):
        return dict(scores)
    smoothed = {}
    for c in LIVE:
        others, coef = models[c]
        x = np.array([1.0] + [float(scores[o]) for o in others])
        implied = float(x @ coef)
        smoothed[c] = blend * implied + (1 - blend) * float(scores[c])
    return smoothed


def correct_and_predict(title, author, genre, scores, conf, resid_sd,
                        books, gw, gcw, cache, blurb="", keywords="",
                        corr_models=None):
    """
    Apply the validated AUTHOR+GENRE hierarchical correction (reference:
    reresearch_and_measure.correct_book, method "author_genre") to the researched
    components, then roll the CORRECTED components up to a WA.

    Correction training data: your rated books (real component scores from the DB)
    paired with their richer-prompt LLM scores (the cache). The target book is
    appended as a row and corrected by training on all the rated rows — i.e. the
    exact reference logic, applied out-of-sample to this book.

    Returns a display dict; `scores` in it are the corrected components that get
    displayed and stored.

    Pipeline: research -> correlation-smooth (when corr_models given) -> author+
    genre correct. The smoothing is a validated preprocessing step that runs on
    the raw LLM scores BEFORE the unchanged correction below.
    """
    # NEW (preprocessing): correlation-smooth the raw LLM scores before correction.
    if corr_models is not None:
        scores = smooth_components(scores, corr_models)

    # Training pairs: your rated books (real) x richer-prompt LLM scores (cache).
    df = rm.build_pairs(books, cache)
    # Never let the target train on itself (e.g. if you research a rated book).
    df = df[df["Book"] != title].reset_index(drop=True)

    # How well-grounded the correction is (the UI reliability signal).
    n_genre = int((df["Genre"] == genre).sum())
    n_author = int((df["Author"] == author).sum())

    # Append THIS book as the target row; only its LLM scores are read by the
    # correction (it trains on the rated rows and is applied to this one).
    newrow = {"Book": title, "Genre": genre, "Author": author}
    for c in LIVE:
        newrow["llm_" + c] = float(scores[c]) if c in scores else np.nan
    df2 = pd.concat([df, pd.DataFrame([newrow])], ignore_index=True)

    # EXACT reference correction, full method.
    corrected = rm.correct_book(df2, len(df2) - 1, "author_genre")
    corrected = {c: float(v) for c, v in corrected.items()}

    wa = _wa_from_components(corrected, genre, gw, gcw)
    half = 1.645 * resid_sd
    ci = (wa - half, wa + half)
    rank = int((books["WA"] > wa).sum() + 1)
    return {
        "title": title, "author": author, "genre": genre,
        "scores": corrected, "wa": wa, "ci": ci, "rank": rank,
        "total": len(books), "n_genre": n_genre, "n_author": n_author,
        "conf": conf, "blurb": blurb, "keywords": keywords,
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
