"""
challenger_features.py
======================
Causal (leakage-safe) feature construction for the TabPFN challenger bake-off.

WHAT THIS IS
------------
For a book at position *t* in walk-forward read order, build a feature row using
ONLY books at positions < t (the "past-only pool"). These rows are the challenger
predictor's inputs; the target it learns to predict is the book's actual WA.

WHY THE FEATURES ARE *RAW*, NOT SHRUNK
--------------------------------------
The whole point of the bake-off (see the brief) is: learned shrinkage (TabPFN
over raw priors) vs. the engine's hand-tuned empirical-Bayes shrinkage. So we
feed the RAW causal prior mean + its support count and let TabPFN discover its
own shrinkage. We deliberately do NOT feed the engine's already-shrunk
`estimate_components` / `_shrink` output — that would hand the challenger the very
thing it is meant to compete against.

LEAKAGE DISCIPLINE (identical to walkforward.py's `honest` variant)
-------------------------------------------------------------------
  * The pool is books read STRICTLY BEFORE the held-out book (positions 1..t-1 in
    Timeline read order).
  * author / genre priors match by EXACT string equality on author / genre — the
    same rule the engine uses (`(df["Author"] == author).sum()`), so a row's
    `author_prior_count` equals the engine's `n_author` for that fold and the
    ">= N prior" subsets line up exactly (mirrors ablation.py).
  * Every field is a property known BEFORE the book is read (author, genre, word
    count, series membership/position) or a summary of earlier reads. Nothing
    peeks at the held-out book's own scores.

FIELDS THAT DO NOT EXIST IN THIS SCHEMA (brief said "if present")
-----------------------------------------------------------------
  * pub_year   — the `books` table has no publication year.
  * page_count — not stored (word count is the only length field: `words`).
  * year_read  — EXISTS but is the *reading* year (only 2025/2026 in the data),
    not a book property; near-constant and it leaks reading position, so it is
    intentionally excluded. `word_count` is the sole length feature.

MISSING VALUES
--------------
TabPFN v2 handles NaN natively, so a thin/absent tier is encoded as a missing
value rather than a magic number:
  * author_prior_mean / genre_prior_mean -> None when the tier has 0 prior books.
  * author_prior_std -> None unless >= 2 prior books by the author.
  * series_position  -> None when the book has no recorded series number.
Counts are always real integers (0 is informative: "first book by this author").

This module is deliberately dependency-light (numpy only) and imports nothing
from the app, so it is unit-testable in isolation and cannot form an import cycle
with the engine or the harness.
"""

import numpy as np

# Canonical feature order == column order of the matrix fed to the challenger.
# Frozen here so the challenger sees a stable, reproducible column layout.
FEATURE_NAMES = [
    "author_prior_mean",    # raw mean WA of prior-read books by this author (None if 0)
    "author_prior_count",   # # prior-read books by this author (== engine n_author)
    "author_prior_std",     # sample std of those WAs (None unless >= 2)
    "genre_prior_mean",     # raw mean WA of prior-read books in this genre (None if 0)
    "genre_prior_count",    # # prior-read books in this genre (== engine n_genre)
    "word_count",           # `words` for this book (None if unknown)
    "series_flag",          # 1 if the book belongs to a named series, else 0
    "series_position",      # series_number (None if unknown, e.g. standalone)
]


def _mean(vals):
    return float(np.mean(vals)) if vals else None


def _std(vals):
    # Sample std (ddof=1); defined only for >= 2 observations.
    return float(np.std(vals, ddof=1)) if len(vals) >= 2 else None


def build_row(target, pool):
    """Build one causal feature row for `target` from its past-only `pool`.

    target : dict with keys `author`, `genre`, `words`, `series`, `series_number`.
             (`title`/`position`/`wa` may also be present; they are not read here.)
    pool   : iterable of dicts for books read STRICTLY BEFORE the target, each with
             `author`, `genre`, `wa`. Records whose `wa` is None are ignored.

    Returns a dict keyed by FEATURE_NAMES (values are float / int / None).
    """
    author, genre = target["author"], target["genre"]

    author_wa = [r["wa"] for r in pool
                 if r["author"] == author and r.get("wa") is not None]
    genre_wa = [r["wa"] for r in pool
                if r["genre"] == genre and r.get("wa") is not None]

    series = target.get("series")
    series_flag = 1 if (series not in (None, "")) else 0
    spos = target.get("series_number")
    words = target.get("words")

    return {
        "author_prior_mean": _mean(author_wa),
        "author_prior_count": len(author_wa),
        "author_prior_std": _std(author_wa),
        "genre_prior_mean": _mean(genre_wa),
        "genre_prior_count": len(genre_wa),
        "word_count": float(words) if words is not None else None,
        "series_flag": series_flag,
        "series_position": float(spos) if spos is not None else None,
    }


def causal_feature_rows(ordered_records):
    """Build causal feature rows for an ENTIRE walk-forward sequence.

    ordered_records : list of book dicts already sorted by walk-forward position
        (each with author, genre, wa, words, series, series_number). Position i's
        pool is exactly ordered_records[:i] — i.e. every strictly-earlier read, so
        the returned rows are the causal features "as of each book's own read
        time." This is what makes the challenger's in-context set honest: the
        context row for a prior book is computed from ITS OWN past, not the query's.

    Returns a list of (record, feature_row) in the same order.
    """
    out = []
    for i, target in enumerate(ordered_records):
        out.append((target, build_row(target, ordered_records[:i])))
    return out


def to_matrix(feature_rows):
    """Convert a list of feature-row dicts into a float ndarray in FEATURE_NAMES
    column order, encoding None as np.nan (TabPFN reads NaN as missing)."""
    m = np.full((len(feature_rows), len(FEATURE_NAMES)), np.nan, dtype=float)
    for i, row in enumerate(feature_rows):
        for j, name in enumerate(FEATURE_NAMES):
            v = row.get(name)
            if v is not None:
                m[i, j] = float(v)
    return m


# ---------------------------------------------------------------------------
# Tiny isolated self-test (no app imports): proves causality + missing-value
# encoding on a hand-built 4-book sequence.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    seq = [
        {"title": "A1", "author": "Alice", "genre": "SF", "wa": 8.0,
         "words": 100000, "series": "", "series_number": None},
        {"title": "A2", "author": "Alice", "genre": "SF", "wa": 9.0,
         "words": 120000, "series": "Foo", "series_number": 1},
        {"title": "B1", "author": "Bob", "genre": "Fantasy", "wa": 6.0,
         "words": 90000, "series": "", "series_number": None},
        {"title": "A3", "author": "Alice", "genre": "Fantasy", "wa": 7.0,
         "words": 110000, "series": "Foo", "series_number": 2},
    ]
    rows = causal_feature_rows(seq)
    for rec, row in rows:
        print(f"{rec['title']:>3}  {row}")

    # Assertions: first book has no priors; A3 sees 2 Alice priors (8,9) and 1
    # Fantasy prior (6); std defined only at >= 2.
    _, r0 = rows[0]
    assert r0["author_prior_count"] == 0 and r0["author_prior_mean"] is None
    assert r0["author_prior_std"] is None
    _, r3 = rows[3]
    assert r3["author_prior_count"] == 2
    assert abs(r3["author_prior_mean"] - 8.5) < 1e-9
    assert abs(r3["author_prior_std"] - np.std([8.0, 9.0], ddof=1)) < 1e-9
    assert r3["genre_prior_count"] == 1 and r3["genre_prior_mean"] == 6.0
    assert r3["series_flag"] == 1 and r3["series_position"] == 2.0
    print("challenger_features self-test: OK")
