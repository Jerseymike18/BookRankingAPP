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
     blended with an author-level deviation, K_GENRE=6 / K_AUTHOR=0.5 shrinkage
     (+ a 0.5 genre slope-lift that de-compresses the fitted regression).

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
import tempfile
import threading

import numpy as np
import pandas as pd

import anthropic
import db_loader
import research_layer as rl
import reresearch_and_measure as rm

CACHE = rm.RICH_CACHE        # "llm_scores_richer.json"
LIVE = rm.LIVE               # canonical 14 components, reference order
DISCOVER_MODEL = "claude-sonnet-4-6"  # candidate generation only; research uses rm.MODEL (Opus)
DISCOVER_MAX = 15            # runaway guard when the candidate count is LLM-inferred

WELL_SAMPLED_GENRE = 5       # genre below this is flagged as lower-reliability grounding
BLEND = 0.2                  # correlation-smoothing weight (validated winning variant)
COLD_START_MIN_POOL = 25     # min word-counted books before the cold-start term is fit


# ---------------------------------------------------------------------------
# Richer-prompt cache (shared with reresearch_and_measure.py)
# ---------------------------------------------------------------------------
def load_cache(path=CACHE):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


_CACHE_LOCK = threading.Lock()


def save_cache(cache, path=CACHE):
    """Atomic, lock-guarded cache write. STEP 5 enables concurrent candidate
    scoring (the Discover flow fires several /api/predict/research calls at once,
    which FastAPI serves on its threadpool), so two writers can hit this together.
    Writing to a temp file in the same dir and os.replace()-ing it in means a
    concurrent reader never sees a half-written JSON file; the lock serialises
    in-process writers. (Lost updates across a separate load/save pair just cause
    a harmless re-research, never corruption.)"""
    with _CACHE_LOCK:
        d = os.path.dirname(os.path.abspath(path)) or "."
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".cache-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(cache, f, indent=2)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


def get_client(key_path="apikey.txt"):
    """Anthropic client for the richer-prompt researcher (raises if key missing)."""
    return anthropic.Anthropic(api_key=rl.load_key(key_path))


# ---------------------------------------------------------------------------
# Durable research cache (DB-backed; see db_write.research_cache)
# ---------------------------------------------------------------------------
# The title-keyed JSON caches are the warm SEED, but their runtime writes are
# EPHEMERAL on Railway (container FS, lost per deploy). These best-effort wrappers
# persist runtime research to the global research_cache table and read it back on a
# file-cache MISS, so a book researched once survives redeploys/instances without a
# repeat ~38-110s web_search. Best-effort BY DESIGN: any DB error is swallowed (the
# prediction still succeeds from the in-memory/file cache). The key is normalized
# (matches rl.cache_lookup) so case/whitespace variants share one durable row.
def db_cache_get(cache_name, title):
    """Durable-store lookup for a title; None on miss or any DB error."""
    try:
        import db_write
        return db_write.get_research_cache(cache_name, rl.normalize_title(title))
    except Exception:
        return None


def db_cache_put(cache_name, title, entry):
    """Persist one research entry to the durable store; silent on any DB error."""
    try:
        import db_write
        db_write.put_research_cache(cache_name, rl.normalize_title(title), entry)
    except Exception:
        pass


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


def _coerce_words(raw):
    """Coerce the model's word-count estimate into a positive int (or None).
    Tolerates '150,000', '150000', '150k', '~150000' and plain numbers."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw) if raw > 0 else None
    s = str(raw).strip().lower().replace(",", "").replace("~", "")
    mult = 1
    if s.endswith("k"):
        mult, s = 1000, s[:-1]
    m = re.search(r"\d+(\.\d+)?", s)
    if not m:
        return None
    val = float(m.group()) * mult
    return int(val) if val > 0 else None


def research_rich_plus(client, title, author, genre, allowed_genres=None):
    """One richer-prompt API call that returns the 14 components, a confidence
    flag, a blurb, keywords, and (Part D) a genre + estimated word count.
    Returns (scores, conf, blurb, keywords, det_genre, words).

    Reuses the validated rich prompt (reresearch_and_measure.rich_prompt) — the
    component scoring is unchanged. When `genre` is None and `allowed_genres` is
    given, the SAME call also picks the book's genre from that list (so the genre
    always matches your schema, never a variant); when a genre is supplied, it is
    used as-is and only the word-count estimate is requested. The word count is
    always an ESTIMATE (the caller shows it as an editable field)."""
    detect_genre = genre is None
    base_genre = genre if genre else "to be determined by you"
    prompt = rm.rich_prompt(title, author, base_genre) + _BLURB_KW_INSTRUCTIONS

    extra = ""
    if detect_genre and allowed_genres:
        glist = ", ".join(sorted(allowed_genres))
        extra += ('\n  "genre": the single best-fitting genre for THIS book, '
                  'chosen EXACTLY from this list (copy the spelling exactly — '
                  'never invent or alter a variant):\n    ' + glist)
    extra += ('\n  "words": your best ESTIMATE of the book\'s total word count as '
              'a single integer (e.g. 150000). This is only an estimate; the user '
              'can edit it.')
    prompt += extra

    msg = client.messages.create(
        model=rm.MODEL, max_tokens=800,
        messages=[{"role": "user", "content": prompt}])
    text = msg.content[0].text.strip()
    data = rl._extract_json(text)
    conf = data.pop("confidence", "unknown")
    blurb = (data.pop("blurb", "") or "").strip()
    keywords = _normalize_keywords(data.pop("keywords", ""))
    det_genre = (str(data.pop("genre", "") or "").strip() or None)
    # Guard the schema constraint: only accept a detected genre that is actually
    # in your list; otherwise leave it None so the caller can fall back.
    if det_genre and allowed_genres and det_genre not in set(allowed_genres):
        det_genre = None
    words = _coerce_words(data.pop("words", None))
    scores = {c: float(data[c]) for c in LIVE if c in data}
    return scores, conf, blurb, keywords, det_genre, words


def research_book(title, author, genre, client, cache, allowed_genres=None):
    """Return (scores, conf, blurb, keywords, det_genre, words, from_cache). On a
    miss, researches with the RICHER prompt (extended to also yield a blurb,
    keywords, a schema-valid genre, and an estimated word count in the same call)
    and adds it to `cache` in place so the book is never re-researched. Cache
    entries written by the batch reference script may predate the blurb/keywords/
    genre/words fields, so those default to empty/None."""
    # Exact-then-normalized lookup: a case/whitespace variant of a cached title
    # still hits, avoiding a needless research call for an already-cached book.
    e = rl.cache_lookup(cache, title)
    if e is not None:
        return (e["scores"], e.get("conf", "?"),
                e.get("blurb", ""), e.get("keywords", ""),
                e.get("genre") or genre, e.get("words"), True)
    # Durable store: a book researched at runtime (persisted below) survives Railway
    # redeploys even though the JSON file write does not. Consulted ONLY on a file
    # miss — one cheap read before a multi-second LLM call — so the hit path is
    # unchanged. Warm the in-memory cache so the rest of this process reuses it.
    e = db_cache_get(CACHE, title)
    if e is not None:
        cache[title] = e
        return (e["scores"], e.get("conf", "?"),
                e.get("blurb", ""), e.get("keywords", ""),
                e.get("genre") or genre, e.get("words"), True)
    scores, conf, blurb, keywords, det_genre, words = research_rich_plus(
        client, title, author, genre, allowed_genres)
    entry = {"scores": scores, "conf": conf, "blurb": blurb, "keywords": keywords,
             "genre": det_genre or genre, "words": words}
    cache[title] = entry
    db_cache_put(CACHE, title, entry)
    return scores, conf, blurb, keywords, det_genre or genre, words, False


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
    data = rl._extract_json(text)
    blurb = (data.get("blurb", "") or "").strip()
    keywords = _normalize_keywords(data.get("keywords", ""))
    return blurb, keywords


def generate_rich_blurb(client, title, author, genre, corrected_scores, wa, ci,
                        n_genre, n_author, read_books, model=rm.MODEL):
    """Produce a rich, opinionated recommendation blurb in the calibrated house
    style, used for predicted books that will be saved to recommendations.

    The house style (4 beats, ~4 sentences, plain prose — no markdown):
      1. Positioning + plot hook — what this book IS and its core premise.
      2. "Closest analog: <BookA> for <reason> + <BookB> for <reason>." — the
         analogs MUST be books the reader has actually read (from read_books).
      3. A thematic line — "A meditation on ..." / "An exploration of ...".
      4. "Confidence caveat: <Component> (predicted X.X) is highest-risk — ..."
         naming the single shakiest component and a plausible range.

    read_books is a list of (title, author, genre) tuples — the reader's rated
    library, the ONLY allowed source for analogs. corrected_scores is the 14
    predicted components; wa/ci frame the confidence. Never raises — returns ""
    on failure so the caller can fall back to the plain research blurb."""
    score_lines = "\n".join(f"  {c}: {corrected_scores[c]:.1f}"
                            for c in LIVE if c in corrected_scores)
    # Compact library list for analog selection (title — author, genre).
    lib_lines = "\n".join(f"  {t} — {a} ({g})" for (t, a, g) in read_books)
    half = (ci[1] - wa) if ci and ci[1] is not None else 0.5

    prompt = f'''You write calibrated, opinionated book-recommendation blurbs for ONE reader,
in a precise house style. Write the blurb for a predicted (not-yet-read) book.

BOOK: "{title}" by {author} (genre: {genre})

PREDICTED COMPONENT SCORES (0-10, this reader's calibrated taste model):
{score_lines}

Predicted Weighted Average: {wa:.2f}  (≈80% interval ±{half:.2f})
Grounding: {n_author} book(s) by this author and {n_genre} in this genre are
already in the reader's library, so confidence is {"high" if (n_author + n_genre) >= 4 else "modest"}.

THE READER'S RATED LIBRARY (the ONLY books you may cite as analogs — never
invent titles or cite books not on this list):
{lib_lines}

Write ONE blurb of about 3-5 sentences, plain prose (no markdown, no line
breaks), following this exact four-beat structure:
1. Positioning + plot hook: what the book is, its standing, and its core premise.
2. "Closest analog: <Book A> for <specific reason> + <Book B> for <specific
   reason>." — both books MUST come from the reader's library above.
3. A thematic line beginning "A meditation on" or "An exploration of".
4. "Confidence caveat: <Component> (predicted <score>) is highest-risk — <one
   clause on why, with a plausible numeric range>." Pick the single component
   you are least certain about and justify it from the book's content.

Respond with ONLY a JSON object — no prose, no markdown:
{{"blurb": "..."}}'''
    try:
        msg = client.messages.create(
            model=model, max_tokens=600,
            messages=[{"role": "user", "content": prompt}])
        data = rl._extract_json(msg.content[0].text.strip())
        return (data.get("blurb", "") or "").strip()
    except Exception:
        return ""


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
                        corr_models=None, words=None, series_number=None,
                        cold_term=None, rank_pool=None):
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

    `rank_pool` (optional): the frame the reader-facing RANK, TOTAL, and grounding
    counts (n_author / n_genre) are measured against — the reader's OWN rated
    library. It is DISTINCT from `books` (the correction pool): a multi-tenant
    cold-start reader borrows the seed's calibrated books for the correction VALUE,
    but must never rank against them, or a fresh account sees "rank #2 of <seed
    corpus>". Defaults to `books`, so single-pool callers (walk-forward,
    test_engine) are byte-identical. The cold-start GATE still keys off the
    correction pool, so prediction VALUES are unchanged regardless of rank_pool.
    """
    # NEW (preprocessing): correlation-smooth the raw LLM scores before correction.
    if corr_models is not None:
        scores = smooth_components(scores, corr_models)

    # Training pairs: your rated books (real) x richer-prompt LLM scores (cache).
    df = rm.build_pairs(books, cache)
    # Never let the target train on itself (e.g. if you research a rated book).
    df = df[df["Book"] != title].reset_index(drop=True)

    # Correction-pool grounding: how many analogs the CORRECTION trained on. This
    # gates the cold-start term below (the correction is author-blind iff its pool
    # holds no same-author book), so it stays on `books` — the (possibly
    # seed-borrowed) correction pool — and prediction VALUES stay unchanged.
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
    # Word-count cold-start terminus term (default OFF; see
    # experiments/cold_start_wordcount_spec.md). Applied ONLY when there is no
    # same-author analog, where the correction is blind to book length. With
    # cold_term=None (the default for every current caller) this is byte-identical
    # to prior behavior, so the 0.636 walk-forward baseline and test_engine hold.
    if cold_term is not None and n_author == 0:
        wa = apply_cold_start_term(wa, words, series_number, author, cold_term)
    half = 1.645 * resid_sd
    ci = (wa - half, wa + half)

    # Reader-facing rank / total / grounding are scoped to `rank_pool` — the reader's
    # OWN rated library — NOT the correction pool. For a multi-tenant cold-start
    # reader the correction borrows the seed's calibrated books, but their predicted
    # rank and "N books by this author" must reflect only what THEY have read.
    # rank_pool defaults to `books`, so single-pool callers stay byte-identical.
    if rank_pool is None:
        rank_frame = books
        disp_n_genre, disp_n_author = n_genre, n_author
    else:
        rank_frame = rank_pool
        disp_n_genre = int((rank_frame["Genre"] == genre).sum())
        disp_n_author = int((rank_frame["Author"] == author).sum())
    rank = int((rank_frame["WA"] > wa).sum() + 1)
    total = int(len(rank_frame))
    return {
        "title": title, "author": author, "genre": genre,
        "scores": corrected, "wa": wa, "ci": ci, "rank": rank,
        "total": total, "n_genre": disp_n_genre, "n_author": disp_n_author,
        "conf": conf, "blurb": blurb, "keywords": keywords,
    }


# ---------------------------------------------------------------------------
# Word-count cold-start terminus term (default OFF; opt-in via correct_and_predict's
# cold_term=). Reference + validation: experiments/cold_start_wordcount_spec.md and
# experiments/validate_cold_term.py. This is an ADDITIVE post-step on the cold slice
# (n_author == 0); it never touches the read-only core (predict_engine / db_loader /
# views / reresearch_and_measure.correct_book).
# ---------------------------------------------------------------------------
def _cold_features(words, series_number, use_series):
    """Raw (uncentered) feature vector for the cold-start term, or None when the word
    count is missing/invalid (term off for that book). Features: log10(words); and,
    when use_series, [series_number|0, in_series flag] (a standalone book -> 0, 0).
    series_number is caller-supplied metadata (db_loader's frame does not carry it and
    is read-only), so this stays decoupled from any DB read."""
    try:
        w = float(words)
    except (TypeError, ValueError):
        return None
    if not w or w <= 0 or (isinstance(w, float) and np.isnan(w)):
        return None
    feats = [np.log10(w)]
    if use_series:
        try:
            snv = float(series_number)
            in_s = 0.0 if np.isnan(snv) else 1.0
        except (TypeError, ValueError):
            snv, in_s = 0.0, 0.0
        feats += [snv if in_s else 0.0, in_s]
    return feats


def normalize_author(name):
    """Loose author-name key for matching a stated favorite against a book's author
    ('J.R.R. Tolkien' == 'jrr tolkien')."""
    return " ".join(str(name).lower().replace(".", " ").split()) if name else ""


def apply_cold_start_term(wa, words, series_number, author, coefs):
    """Return wa adjusted by the cold-start term, or wa unchanged when the term is off
    (coefs is None). The term may carry two independent, additive components, each optional:
      * a word-count slope (fitted, or a new user's stated preference);
      * an author prior — {"map": {normalized_author: weight}, "base": offset} — a new
        reader's favorite authors (weight 1.0) and their analogs (discounted), a positive
        offset when the book's author is on the list. Applied only on the cold slice by the
        caller (n_author==0). The adjusted WA is clamped to [0, 10]."""
    if coefs is None:
        return wa
    adj = 0.0
    if coefs.get("slopes"):                         # word-count component
        f = _cold_features(words, series_number, coefs.get("use_series", False))
        if f is not None:
            fc = np.array(f) - np.array(coefs["mu"])
            adj += coefs.get("intercept", 0.0) + float(np.dot(coefs["slopes"], fc))
    ap = coefs.get("author_prior")                  # author-prior component (new users)
    if ap and author:
        adj += float(ap.get("base", 0.0)) * float(ap.get("map", {}).get(
            normalize_author(author), 0.0))
    return float(min(max(wa + adj, 0.0), 10.0))


def fit_cold_start_term(books, cache, gw, gcw, corr_models=None,
                        min_pool=COLD_START_MIN_POOL, series_map=None):
    """Fit the cold-start term on a pool: OLS of the correction's leave-one-out residual
    (actual_WA - honest_WA) on the centered features from `_cold_features`. When
    `series_map` ({title: series_number}) is given, series features are included.

    correct_and_predict excludes the target row internally, so each per-book call is a
    genuine LOO prediction. Fit on ALL word-counted pool books (applied only on the cold
    slice by the caller). Returns {"intercept","slopes","mu","use_series","n"} or None
    when fewer than `min_pool` usable books (term stays OFF)."""
    use_series = series_map is not None
    X, y = [], []
    for _, b in books.iterrows():
        title = b["Book"]
        entry = cache.get(title)
        scores = entry.get("scores") if isinstance(entry, dict) else None
        if not isinstance(scores, dict) or any(c not in scores for c in LIVE):
            continue
        f = _cold_features(b.get("Words"),
                           (series_map or {}).get(title), use_series)
        if f is None:
            continue
        res = correct_and_predict(title, b["Author"], b["Genre"], dict(scores),
                                  entry.get("conf", "?"), 0.0, books, gw, gcw, cache,
                                  corr_models=corr_models)   # cold_term omitted -> off
        X.append(f)
        y.append(float(b["WA"]) - res["wa"])
    if len(X) < min_pool:
        return None
    X, y = np.array(X, dtype=float), np.array(y, dtype=float)
    mu = X.mean(axis=0)
    A = np.column_stack([np.ones(len(X)), X - mu])
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    return {"intercept": float(coef[0]), "slopes": [float(c) for c in coef[1:]],
            "mu": [float(m) for m in mu], "use_series": use_series, "n": len(X)}


def find_author_analogs(favorites, client, per=3, model=DISCOVER_MODEL):
    """Widen a new reader's favorite-author list to stylistically-similar authors, so the
    cold-start author prior covers more unread books (analogs get a discounted offset).
    One cheap LLM call → {favorite: [analog names]}. Best-effort: returns {} on any
    failure so the caller simply falls back to the stated favorites alone."""
    favs = [str(a).strip() for a in (favorites or []) if str(a).strip()][:8]
    if not favs:
        return {}
    prompt = f'''For each author below, list up to {per} DIFFERENT, widely-recognized
authors whose books are stylistically similar — a reader who loves the first would likely
enjoy them. Do NOT repeat any of the input authors.

AUTHORS: {"; ".join(favs)}

Respond with ONLY a JSON object mapping each input author to an array of similar author
names (no prose, no markdown):
{{"Author One": ["Similar A", "Similar B"]}}'''
    try:
        msg = client.messages.create(
            model=model, max_tokens=600,
            messages=[{"role": "user", "content": prompt}])
        data = rl._extract_json(msg.content[0].text.strip())
    except Exception:
        return {}
    out = {}
    for fav in favs:
        sims = data.get(fav) if isinstance(data, dict) else None
        out[fav] = ([str(s).strip() for s in sims if str(s).strip()][:per]
                    if isinstance(sims, list) else [])
    return out


# ---------------------------------------------------------------------------
# Prediction-mechanism metadata (READ-ONLY) — reconstruct, for one prediction,
# the dimensions delta_log now records so residuals can later be grouped by HOW
# a prediction was made, not just by which book. Called by backend._maybe_log_delta
# when a forecast book is finally rated, so the metadata lands on the delta_log
# row alongside predicted-vs-actual.
#
# Faithfulness: every value comes from the SAME persisted inputs and reference
# functions the prediction itself used — build_pairs / correct_book (the exact
# author_genre correction) / _wa_from_components / the engine's resid_sd. No
# engine math is reimplemented and nothing is written. The correction split is
# read straight off correct_book's own ladder: 'raw' (post-smoothing baseline),
# 'genre_reg' (adds the genre layer), 'author_genre' (adds the author layer) —
# so corr_genre + corr_author == corr_wa by construction.
#
# Every field degrades to absent on missing inputs (e.g. a hand-scored book with
# no cached raw LLM scores gets genre/author/words/analog counts/CI but no
# correction split or conf), so partial metadata still logs rather than nothing.
#
# NOTE ON TIMING: this runs when the book is READ, re-deriving against the
# current library. correct_and_predict excludes the target row from its training
# pool (df[df.Book != title]); we replicate that, so counts/corrections match the
# prediction closely. Any residual drift is only from books added between predict
# and read — negligible for resid_sd (global, stable) and small for the n counts.
# ---------------------------------------------------------------------------
def build_prediction_meta(title, author, genre, words, pred_wa, resid_sd,
                          books, gw, gcw, cache, corr_models=None):
    """Return a dict of delta_log mechanism-metadata columns for this prediction.
    Keys map 1:1 to db_write.DELTA_META_COLUMNS; any that cannot be derived are
    omitted. Never raises for a single-field failure — best-effort per field."""
    meta = {"pred_genre": genre, "pred_author": author}
    try:
        meta["pred_words"] = int(words) if words not in (None, "") else None
    except (TypeError, ValueError):
        pass

    # Confidence/CI at prediction time. The half-width is the engine's own
    # 1.645 * resid_sd (identical to correct_and_predict); the centre is pred_wa.
    try:
        half = 1.645 * float(resid_sd)
        meta["ci_low"] = round(float(pred_wa) - half, 4)
        meta["ci_high"] = round(float(pred_wa) + half, 4)
        meta["ci_width"] = round(2 * half, 4)
    except (TypeError, ValueError):
        pass

    entry = cache.get(title) if isinstance(cache, dict) else None
    raw = entry.get("scores") if isinstance(entry, dict) else None
    if isinstance(entry, dict) and entry.get("conf"):
        meta["conf"] = entry["conf"]

    # Grounding counts = analog blend weights (author layer n/(n+K_AUTHOR), genre
    # layer n/(n+K_GENRE)). Same training pool as correct_and_predict, target row
    # excluded. analog_src names the tightest pool that actually fired.
    df = None
    try:
        df = rm.build_pairs(books, cache)
        df = df[df["Book"] != title].reset_index(drop=True)
        n_genre = int((df["Genre"] == genre).sum())
        n_author = int((df["Author"] == author).sum())
        meta["n_genre"] = n_genre
        meta["n_author"] = n_author
        meta["analog_src"] = ("author" if n_author > 0
                              else "genre" if n_genre > 0 else "global")
    except Exception:
        df = None

    # Correction split, read off correct_book's ladder on THIS book's cached raw
    # LLM scores (smoothed first when corr_models given, exactly as production).
    try:
        if df is not None and isinstance(raw, dict) and all(c in raw for c in LIVE):
            smoothed = smooth_components(raw, corr_models) if corr_models else dict(raw)
            newrow = {"Book": title, "Genre": genre, "Author": author}
            for c in LIVE:
                newrow["llm_" + c] = float(smoothed[c])
            df2 = pd.concat([df, pd.DataFrame([newrow])], ignore_index=True)
            i = len(df2) - 1
            base_wa = _wa_from_components(rm.correct_book(df2, i, "raw"), genre, gw, gcw)
            gen_wa  = _wa_from_components(rm.correct_book(df2, i, "genre_reg"), genre, gw, gcw)
            full_wa = _wa_from_components(rm.correct_book(df2, i, "author_genre"), genre, gw, gcw)
            meta["corr_genre"] = round(gen_wa - base_wa, 4)
            meta["corr_author"] = round(full_wa - gen_wa, 4)
            meta["corr_wa"] = round(full_wa - base_wa, 4)
            # DeltaTracker per-component correction is a manual Excel P-step and
            # is NOT wired into this coded path (see residual_bias_diagnostic.py),
            # so it is left NULL here rather than fabricated.
            meta["corr_dtracker"] = None
            meta["corr_method"] = ("corr_smooth+author_genre" if corr_models
                                   else "author_genre")
    except Exception:
        pass

    return meta


# ---------------------------------------------------------------------------
# Discover: idea-generation step. One API call asks the LLM to PROPOSE candidate
# books for a free-text request, aimed at your taste and avoiding what you've
# read. It returns candidates ONLY (no scores, no opinions) — your engine scores
# them downstream via research_book + correct_and_predict, exactly like the
# single-book Predict flow. Genres are constrained to your schema, mirroring the
# auto-genre feature.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Goodreads-grounded series enumeration (Discover)
# ---------------------------------------------------------------------------
# Sonnet 4.6 (DISCOVER_MODEL) supports the dynamic-filtering web_search tool.
# We DO NOT fetch goodreads.com directly (blocked by the allowlist + anti-
# scraping). Instead we run the model's server-side web_search restricted to
# goodreads.com and have it EXTRACT "Title (Series, #N)" data from the returned
# Goodreads result snippets. Titles/ordinals come from search results, never
# from model memory.
WEB_SEARCH_TOOL = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": 5,
    "allowed_domains": ["goodreads.com"],
}


def _coerce_series_number(raw):
    """Parse a Goodreads ordinal ('1', '0.5', 2) -> int/float, else None."""
    if raw is None:
        return None
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


def _classify_series_request(request, client, model):
    """Decide if `request` is a single-series enumeration, and its scope.

    Returns {"is_series": bool, "series_name": str, "author": str,
    "scope": "main"|"all"}. Mood/theme/genre requests classify is_series=False
    (those use the normal generator and are NOT web-searched)."""
    prompt = f'''You are routing a book request. Decide whether it asks to ENUMERATE the books of ONE specific named series (optionally by a named author) — e.g. "all books in The Stormlight Archive", "main books of The Bound and the Broken by Ryan Cahill", "Wheel of Time in order".

A request for a MOOD, THEME, GENRE, or general recommendation ("3 cozy mysteries", "something like Dune", "uplifting sci-fi") is NOT a series enumeration.

If — and only if — it IS a series enumeration, extract the series name, the author if one is named (else ""), and the scope:
- "main" -> only the main-sequence novels (exclude novellas / .5 entries)
- "all"  -> every entry including novellas and short stories
Default scope to "main" unless the request clearly asks for everything ("all books", "including novellas", "complete").

REQUEST: {request}

Respond with ONLY a JSON object — no prose, no markdown:
{{"is_series": true, "series_name": "...", "author": "...", "scope": "main"}}'''
    try:
        msg = client.messages.create(
            model=model, max_tokens=400,
            messages=[{"role": "user", "content": prompt}])
        data = rl._extract_json(msg.content[0].text.strip())
    except Exception:
        return {"is_series": False, "series_name": "", "author": "", "scope": "main"}
    scope = str(data.get("scope", "main")).strip().lower()
    return {
        "is_series": bool(data.get("is_series")),
        "series_name": str(data.get("series_name", "") or "").strip(),
        "author": str(data.get("author", "") or "").strip(),
        "scope": scope if scope in ("main", "all") else "main",
    }


def _web_search_json(prompt, client, model, max_continuations=4):
    """Run a goodreads-restricted web_search turn; return (data, source_urls).

    Handles the server-tool `pause_turn` loop and collects the Goodreads result
    URLs (provenance). `data` is the parsed final JSON, or {} if unreadable."""
    messages = [{"role": "user", "content": prompt}]
    sources, resp = [], None
    for _ in range(max_continuations):
        resp = client.messages.create(
            model=model, max_tokens=3000, tools=[WEB_SEARCH_TOOL],
            messages=messages)
        for block in resp.content:
            # web_search_tool_result.content is a LIST of results on success,
            # or a single error object on failure — only harvest URLs from lists.
            if getattr(block, "type", None) == "web_search_tool_result":
                results = getattr(block, "content", None)
                if isinstance(results, list):
                    for r in results:
                        url = getattr(r, "url", None)
                        if url:
                            sources.append(url)
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        break
    text = "".join(getattr(b, "text", "") for b in (resp.content if resp else [])
                   if getattr(b, "type", None) == "text")
    try:
        data = rl._extract_json(text)
    except Exception:
        data = {}
    # De-dup source URLs, preserve order.
    seen_u, uniq = set(), []
    for u in sources:
        if u not in seen_u:
            seen_u.add(u)
            uniq.append(u)
    return data, uniq


def _generate_series_candidates(request, series_name, author, scope, allowed_set,
                                genre_list, avoid_titles, client, model):
    """Goodreads-grounded series enumeration -> {candidates, note, sources}.

    Each candidate carries series + series_number (from the Goodreads
    "(Series, #N)" pattern) and a kind label. Scope "main" drops novellas
    (.5 entries); "all" keeps them, labeled. Already-read/saved titles are
    dropped as a backstop AFTER extraction."""
    by_author = f' by {author}' if author else ""
    prompt = f'''You are compiling the books of a single series for a reader, grounded ONLY in Goodreads data.

SERIES: {series_name}{by_author}
SCOPE: {scope}  ("main" = main-sequence novels only; "all" = every entry including novellas / short stories)

Use the web_search tool (it is restricted to goodreads.com) to find the Goodreads series page and its book list. Goodreads lists each entry as "Title (Series Name, #N)" — the #N is the canonical series ordinal; novellas and shorts use fractional numbers like #0.5, #1.5, #2.5.

Extract EVERY entry you can find IN THE GOODREADS RESULTS. Do NOT invent titles or ordinals: if a book — or its number — is not present in the Goodreads results, omit it rather than guessing.

For each entry provide:
- "title": the book title
- "author": the author
- "series": the canonical Goodreads series name
- "series_number": the numeric ordinal from the "(Series, #N)" pattern (e.g. 1, 0.5, 2.5)
- "kind": "novel" for whole-number entries, "novella" or "short" for fractional (.5/.6) entries
- "genre": the single best-fitting genre chosen EXACTLY from this list (copy the spelling exactly): {genre_list}

After searching, respond with ONLY a JSON object — no prose, no markdown:
{{"books": [{{"title": "...", "author": "...", "series": "...", "series_number": 1, "kind": "novel", "genre": "..."}}],
  "complete": true,
  "note": "short caveat if the Goodreads results were incomplete, else empty"}}'''

    data, sources = _web_search_json(prompt, client, model)

    kept, seen = [], set()
    for b in data.get("books", []):
        title = str(b.get("title", "") or "").strip()
        if not title:
            continue
        key = title.lower()
        if key in avoid_titles or key in seen:
            continue          # backstop: never re-suggest read/saved titles
        num = _coerce_series_number(b.get("series_number"))
        kind = str(b.get("kind", "") or "").strip().lower()
        fractional = (num is not None) and (not float(num).is_integer())
        is_novella = kind in ("novella", "short") or fractional
        if scope == "main" and is_novella:
            continue          # main scope -> drop novellas / .5 entries
        seen.add(key)
        genre = str(b.get("genre", "") or "").strip() or None
        if genre and genre not in allowed_set:
            genre = None
        series = str(b.get("series", "") or "").strip() or series_name or None
        kept.append({
            "title": title,
            "author": str(b.get("author", "") or "").strip() or author,
            "genre": genre,
            "series": series,
            "series_number": num,
            "kind": kind or ("novella" if is_novella else "novel"),
        })

    # Reading order: by ordinal, unknown ordinals last.
    kept.sort(key=lambda c: (c["series_number"] is None,
                             c["series_number"] if c["series_number"] is not None else 0))

    note = str(data.get("note", "") or "").strip()
    if not kept and not note:
        note = (f"Couldn't find Goodreads entries for \"{series_name}\" — "
                f"try naming the author too.")
    elif not data.get("complete", True) and not note:
        note = "Goodreads results may be incomplete for this series."
    return {"candidates": kept, "note": note, "sources": sources}


def generate_candidates(request, allowed_genres, read_books, tbr_books=(), n=None,
                        client=None, model=DISCOVER_MODEL, key_path="apikey.txt"):
    """Return a list of {"title","author","genre"} candidate books for `request`.

    - `n`: how many candidates to return. When None (the default), the model
      infers the count from the REQUEST wording — an explicit number ("the 5
      main books of X", "3 cozy mysteries") is honoured, a single named book
      yields one candidate, and a vague mood request yields a sensible handful.
      An internal cap of ``DISCOVER_MAX`` bounds runaway responses. Pass an
      integer to force an exact target (with a single short top-up retry).

    - `allowed_genres`: your genre list; each candidate's genre is chosen EXACTLY
      from it (a genre outside the list is set to None so the scoring step can
      auto-detect, exactly as single-book research does).
    - `read_books`: iterable of (title, author) you've already rated.
    - `tbr_books`: iterable of (title, author) already on your to-read list (the
      recommendations table), so Discover won't re-suggest something you've
      already saved (which would also fail to save as a duplicate).

    Both lists are sent to the model so it avoids the EXACT titles, and any
    returned candidate whose title matches either list is filtered out as a
    backstop. The exclusion is per-title only: if the reader is partway through a
    series, the model is explicitly invited to suggest the OTHER entries.

    This GENERATES ideas only — it does not score or rank. Returns a dict
    ``{"candidates": [...], "note": "...", "sources": [...]}``. Each candidate
    carries ``series`` and ``series_number`` (both nullable). For single-series
    enumeration requests these come from Goodreads search results (see
    ``_generate_series_candidates``) and ``sources`` lists the Goodreads URLs
    used; for mood/theme requests they are None and ``sources`` is empty.
    ``note`` is empty on a full result and carries a short, machine-readable
    caveat when fewer books than expected could be found."""
    if client is None:
        client = anthropic.Anthropic(api_key=rl.load_key(key_path))
    allowed = list(allowed_genres)
    allowed_set = set(allowed)
    genre_list = ", ".join(sorted(allowed))
    read_pairs = list(read_books)
    tbr_pairs = list(tbr_books)
    # Backstop: never re-suggest the EXACT titles already read or saved.
    avoid_titles = {str(t).strip().lower() for t, _ in read_pairs}
    avoid_titles |= {str(t).strip().lower() for t, _ in tbr_pairs}

    # Route single-series enumerations through the Goodreads-grounded path so
    # titles/ordinals come from search results, not model memory. Mood/theme/
    # genre requests fall through to the normal generator (no web search).
    cls = _classify_series_request(request, client, model)
    if cls["is_series"] and cls["series_name"]:
        return _generate_series_candidates(
            request, cls["series_name"], cls["author"], cls["scope"],
            allowed_set, genre_list, avoid_titles, client, model)

    # Softened avoidance: exclude ONLY the specific titles listed — do NOT steer
    # the model away from related books. This is what lets series continuations
    # surface when the reader is mid-way through a series.
    avoid_sections = (
        "The reader has ALREADY read the exact titles below. Do NOT suggest any "
        "of these specific titles. This is a per-title exclusion ONLY — if the "
        "reader is partway through a series, you SHOULD still suggest the OTHER "
        "books in that series (and adjacent books by the same author) that are "
        "NOT on this list:\n"
        + "\n".join(f"- {t} ({a})" for t, a in read_pairs))
    if tbr_pairs:
        avoid_sections += (
            "\n\nThe reader has also ALREADY saved these exact titles to their "
            "to-read list — do not repeat these specific titles either (other "
            "books in the same series or by the same author remain fair game):\n"
            + "\n".join(f"- {t} ({a})" for t, a in tbr_pairs))

    def _filter(raw_candidates, seen):
        """Drop already-read/saved titles, in-batch dups, and bad genres."""
        kept = []
        for c in raw_candidates:
            title = str(c.get("title", "") or "").strip()
            author = str(c.get("author", "") or "").strip()
            if not title:
                continue
            key = title.lower()
            if key in avoid_titles or key in seen:
                continue          # backstop: never re-suggest read/saved books
            seen.add(key)
            genre = str(c.get("genre", "") or "").strip() or None
            if genre and genre not in allowed_set:
                genre = None      # outside your schema -> auto-detect at scoring
            # Non-series mood/theme requests carry no series metadata.
            kept.append({"title": title, "author": author, "genre": genre,
                         "series": None, "series_number": None})
        return kept

    def _ask(want, extra_exclude=()):
        extra = ""
        if extra_exclude:
            extra = (
                "\n\nYou have ALREADY proposed the titles below in a previous "
                "round — return DIFFERENT books this time, do not repeat any of "
                "these:\n" + "\n".join(f"- {t}" for t in extra_exclude))
        if want is None:
            count_instr = (
                "Decide HOW MANY books to return FROM THE REQUEST itself: if it "
                "states or implies a specific number (e.g. \"the 5 main books of "
                "X\" -> 5, \"3 cozy mysteries\" -> 3), return exactly that many; "
                "if it names a single specific book to predict, return just that "
                "one book; otherwise return a sensible handful (about 5-8). "
                f"Never return more than {DISCOVER_MAX} books.")
        else:
            count_instr = f"Suggest up to {want} books that fit the REQUEST."
        prompt = f'''You are proposing candidate books for a reader with specific, consistent taste. They will run your suggestions through THEIR OWN scoring engine, so propose CANDIDATES only — no reviews, ratings, or opinions.

REQUEST: {request}

Choose each book's genre EXACTLY from this list (copy the spelling exactly — never invent or alter a variant):
{genre_list}

{avoid_sections}{extra}

Aim for fresh suggestions matched to the request and their taste. {count_instr} For each give its title, author, and best-fitting genre from the list above.

Respond with ONLY a JSON object — no prose, no markdown:
{{"candidates": [{{"title": "...", "author": "...", "genre": "..."}}]}}'''
        msg = client.messages.create(
            model=model, max_tokens=1200,
            messages=[{"role": "user", "content": prompt}])
        data = rl._extract_json(msg.content[0].text.strip())
        return data.get("candidates", [])

    seen = set()

    # Inferred-count path: trust the model's read of the request; just guard
    # against a runaway list. No top-up retry — there's no fixed target to hit.
    if n is None:
        out = _filter(_ask(None), seen)
        return {"candidates": out[:DISCOVER_MAX], "note": "", "sources": []}

    out = _filter(_ask(n), seen)

    # Single top-up retry: if the first pass came back materially short (often
    # because the request overlaps the library and survivors got filtered), ask
    # once more for DIFFERENT titles, passing what we already have. Capped at one
    # extra call to bound cost/latency.
    if len(out) < n:
        more = _ask(n - len(out), extra_exclude=[c["title"] for c in out])
        out.extend(_filter(more, seen))

    note = ""
    if len(out) < n:
        note = (f"Only {len(out)} found — the model may not know more titles "
                f"matching this request.")
    return {"candidates": out[:n], "note": note, "sources": []}


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
    data = rl._extract_json(text)
    return (data.get("books", []),
            bool(data.get("complete", False)),
            data.get("note", ""))
