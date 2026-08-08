"""
star_priors.py — per-genre taste offsets derived from Goodreads star ratings
===========================================================================
A reader who imports a Goodreads export arrives with hundreds of 1-5 star
ratings and zero 14-component ratings. Those stars are far too coarse to
synthesise component scores from (never do that — it would put invented numbers
into their tier list and stats), but they DO carry relative preference: "this
reader rates Epic Fantasy above their own average, Literary Fiction below it".

This module turns staged `read` rows into that signal, as shrunken per-genre
offsets in STAR UNITS:

    off[g] = (mean(star | g) - mean(star)) * n_g / (n_g + K_STAR_GENRE)

Empirical-Bayes shrinkage, the same shape the engine uses everywhere else — a
genre with two ratings barely moves, one with thirty moves nearly all the way.

WHERE IT PLUGS IN (and what it deliberately is NOT)
---------------------------------------------------
The engine already has a per-genre cold-start slot: apply_cold_start_term's
``genre_prior`` = {"base": offset, "map": {normalized_genre: weight}}, applied as
``base * map[genre]`` on the n_genre == 0 slice only. Today that slot holds
SELF-REPORTED BINARY favorites (every named favorite weight 1.0, base 0.5). This
module fills the SAME slot with graded, evidence-based numbers.

So there is no new prediction math here and no engine change: the fade is already
correct (the prior vanishes per-genre the moment the reader rates a book in that
genre), and the arithmetic is already what we want.

WHY GENRE ONLY
--------------
The per-AUTHOR version was measured and rejected (2026-08-08): only ~9% of a
reader's to-read list is by an author with 2+ star-rated books, so the prior
would reach almost nothing. Genre reaches ~91%. Do not reintroduce an author
variant without new coverage evidence.

SIGNED, AND THAT MATTERS
------------------------
Unlike the favorites prior, these offsets are SIGNED — a genre the reader rates
below their own average produces a NEGATIVE offset and correctly pushes a
cold-slice prediction down. They are capped (see ``STAR_GENRE_CAP``) so a coarse
5-level signal can never move a prediction further than the favorites prior it
replaces already could.

CALIBRATION
-----------
``STAR_GENRE_SCALE`` converts a star-unit offset into WA points. 1.0 is measured,
not guessed: experiments/star_offset_ceiling.py and star_offset_real.py both put
one star-offset at ~1.0 WA points, consistently across author and genre cuts.

Pure and dependency-free (stdlib only, no project imports) so it stays trivially
testable, exactly like goodreads_import.py.
"""

# Shrinkage constant. Matches predict_engine.genre_bias_and_trust's own n/(n+8)
# genre-trust constant, so this shrinkage and the engine's have the same shape.
K_STAR_GENRE = 8.0

# A genre needs at least this many star-rated books to get an offset at all.
# 3 was chosen from measured to-read coverage: 90.9% at 3, 76.4% at 5.
MIN_N_GENRE = 3

# Star-offset -> WA points. Measured (see module docstring).
STAR_GENRE_SCALE = 1.0

# Hard cap on |offset| in WA points, applied after scaling. Equal to the
# favorites prior's _GENRE_OFFSET_BASE, so replacing that prior can never move a
# prediction further than it already could.
STAR_GENRE_CAP = 0.5


def genre_offsets(rows, k=K_STAR_GENRE, min_n=MIN_N_GENRE):
    """{genre: shrunken offset in STAR units} from staged import rows.

    ``rows`` are dicts with at least ``genre`` and ``goodreads_rating`` (the shape
    db_write.get_staging_rows returns). Rows missing either, or carrying a rating
    outside 1-5, are ignored — an unrated Goodreads row is 0, which means "no
    opinion", NOT "zero stars", and must never be averaged in.

    Returns {} when nothing qualifies, which callers treat as "no prior".
    """
    by_genre = {}
    total, n_total = 0.0, 0
    for r in rows or []:
        genre = (r.get("genre") or "").strip()
        raw = r.get("goodreads_rating")
        if not genre or raw is None:
            continue
        try:
            star = float(raw)
        except (TypeError, ValueError):
            continue
        if not (1.0 <= star <= 5.0):     # 0 == unrated; anything else is corrupt
            continue
        by_genre.setdefault(genre, []).append(star)
        total += star
        n_total += 1

    if n_total < min_n:
        return {}
    grand = total / n_total

    out = {}
    for genre, stars in by_genre.items():
        n = len(stars)
        if n < min_n:
            continue
        out[genre] = ((sum(stars) / n) - grand) * n / (n + k)
    return out


def to_genre_prior(offsets, normalize, scale=STAR_GENRE_SCALE, cap=STAR_GENRE_CAP):
    """Shape {genre: star-offset} into apply_cold_start_term's ``genre_prior``.

    ``normalize`` is research_predict.normalize_genre (injected so this module
    keeps zero project imports). Emits ``base`` = 1.0 with the WA offsets folded
    into the map, rather than base=scale with star units in the map: the capping
    has to happen in WA space, and a per-genre cap cannot be expressed through a
    single shared ``base``.

    Returns None when there is nothing to apply (so callers can fall back to the
    favorites prior).
    """
    m = {}
    for genre, off in (offsets or {}).items():
        ng = normalize(genre)
        if not ng:
            continue
        wa = max(-cap, min(cap, float(off) * scale))
        m[ng] = wa
    if not m:
        return None
    return {"base": 1.0, "map": m}
