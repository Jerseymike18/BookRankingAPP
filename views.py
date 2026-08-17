"""
views.py — derived, live-computed views over your rated library
===============================================================
Every function here is READ-ONLY and stateless: it takes the books DataFrame
that db_loader.load_from_db() returns and computes a view on the fly. Nothing is
stored or duplicated, so these views can never desync from the underlying data —
they recompute from the same components/weights the engine uses.

WHAT'S HERE
-----------
  total_average(...)   : the "Total Average" — the unweighted mean of the five
                         category averages (each a plain mean of its components).
                         Distinct from WA (the genre-weighted average).
  add_total_average(...): attach a "Total Average" column to a books frame.
  tier_bands(...)      : group rows into S+/S/A/B/C/D/F bands (the TierList port).
  series_aggregate(...): per-series rollup (avg Total Average, avg WA, count)
                         plus the series-quality score — see the model notes
                         above series_quality_terms().
  reading_stats(...)   : the BookTracker summary + genre/author rollups.
  timeline(...)        : per-year books, avg WA, and the five category averages.

The category grouping (Story/Character/Aesthetics/Theme/Worldbuilding and which
components belong to each) is read from books.attrs["category_components"], so it
tracks the schema automatically — exactly like the rest of the engine.
"""

import numpy as np
import pandas as pd

# The five categories, in the canonical display order, mapped to the plain
# category-average label used in the Timeline / stats views.
CATEGORY_ORDER = ["Story", "Character", "Aesthetics", "Theme", "Worldbuilding"]

# Band definition shared by the book and series tier lists: S+ is threshold-based
# (a fixed Total-Average cutoff), the rest are percentile bands over what remains.
_BAND_FRACTIONS = [("S", 0.09), ("A", 0.15), ("B", 0.25),
                   ("C", 0.25), ("D", 0.15), ("F", 0.10)]
TIER_ORDER = ["S+", "S", "A", "B", "C", "D", "F"]


# ---------------------------------------------------------------------------
# Total Average — the unweighted mean of the five category averages.
# ---------------------------------------------------------------------------
def _category_components(books):
    return books.attrs.get("category_components", {})


def total_average(row, cat_comps):
    """Total Average for one book: average the per-category plain means, skipping
    any category with no scored components (e.g. Worldbuilding for realist
    genres). Matches the spreadsheet's 'Total Average' column."""
    cat_means = []
    for cat in CATEGORY_ORDER:
        comps = cat_comps.get(cat, [])
        vals = [row[c] for c in comps
                if c in row and pd.notna(row[c])]
        if vals:
            cat_means.append(float(np.mean(vals)))
    return float(np.mean(cat_means)) if cat_means else float("nan")


def category_average(row, cat, cat_comps):
    """Plain mean of one category's scored components (NaN if none)."""
    comps = cat_comps.get(cat, [])
    vals = [row[c] for c in comps if c in row and pd.notna(row[c])]
    return float(np.mean(vals)) if vals else float("nan")


def add_total_average(books):
    """Return a copy of `books` with a 'Total Average' column attached."""
    cat_comps = _category_components(books)
    out = books.copy()
    out["Total Average"] = out.apply(lambda r: total_average(r, cat_comps), axis=1)
    return out


# ---------------------------------------------------------------------------
# Tier banding — S+/S/A/B/C/D/F (the TierList sheet port).
# ---------------------------------------------------------------------------
def tier_bands(df, score_col="Total Average", splus_threshold=9.5):
    """Assign each row a tier. S+ = score >= splus_threshold; the remaining rows
    are ranked by score (descending) and split into S/A/B/C/D/F by the percentile
    bands (~9/15/25/25/15/10%). Returns a copy sorted best-first with a 'Tier'
    column. Counts per tier follow from the data — call tier_counts() to display
    them."""
    out = df.sort_values(score_col, ascending=False).reset_index(drop=True)
    n = len(out)
    n_splus = int((out[score_col] >= splus_threshold).sum())
    remaining = n - n_splus

    # Cumulative row-index boundaries for each band, over the non-S+ rows.
    bounds, acc = [], 0.0
    for name, frac in _BAND_FRACTIONS:
        acc += frac
        bounds.append((name, int(round(acc * remaining))))

    labels = []
    for i in range(n):
        if i < n_splus:
            labels.append("S+")
            continue
        j = i - n_splus
        placed = "F"
        for name, b in bounds:
            if j < b:
                placed = name
                break
        labels.append(placed)
    out["Tier"] = labels
    return out


def tier_counts(df_with_tier):
    """Ordered {tier: count} for a frame that already has a 'Tier' column."""
    vc = df_with_tier["Tier"].value_counts().to_dict()
    return {t: int(vc.get(t, 0)) for t in TIER_ORDER}


# ---------------------------------------------------------------------------
# Series aggregation — per-series rollup, ranked by average WA.
# ---------------------------------------------------------------------------
# Series-like markers that are really "no series" and should be excluded.
_NON_SERIES = {"", "standalone", "none", "n/a"}


# --- The series-quality model -----------------------------------------------
# A series is scored as `Avg WA + Consistency + Peak + Finale`, minus an
# insufficient-evidence penalty.
#
# Avg WA is the base: a series of great books should rank above a series of
# mediocre ones, and that ordering is already correct. The modifiers exist
# because a mean over books is STRUCTURALLY BLIND to most of what makes a series
# a series rather than a pile of novels — it is order-invariant and
# spread-invariant. Each term measures something the mean cannot see:
#
#   Consistency  whether it sustained EXCELLENCE, judged by its weakest volume
#   Peak         whether it ever produced a standout   (mean flattens the top)
#   Finale       whether it stuck the landing          (mean ignores position)
#
# WHY THERE IS NO LENGTH BONUS (owner decision, 2026-08-17). The model used to
# carry a compounding "Commitment" bonus for long series. That was wrong for this
# reader, who FINISHES every series they start: length is then a fact about how
# much they read, not evidence that the series was good, and the exponential bonus
# was the single largest modifier in the model (+0.532 for a 15-book series —
# more than any other term's entire cap). It reliably lifted long, uneven series
# above short excellent ones. Measured on this library the old term correlated
# +0.880 with book count and only +0.240 with the series' average WA; Consistency
# correlates -0.003 with count. Do not reintroduce a length reward.
#
# Consistency also ABSORBED a former "Floor" term (avg WA - min WA, a penalty for
# containing a dud). Both are about the weakest volume, so keeping both charged a
# bad book twice — they correlated -0.54. The absolute form is strictly stronger:
# a relative floor scores a uniformly mediocre series as perfectly consistent,
# while "your worst book still beat 72% of what you read" does not.

# Consistency: where the series' WEAKEST volume lands in the reader's own rated
# library, as a percentile centred on the median and rescaled to [-1, +1]. Using
# a percentile rather than a raw WA gap makes the term scale-free — it means the
# same thing for a harsh rater and a generous one. Shrunk by n/(n+K) because two
# good books are weaker evidence of consistency than ten.
_CONSISTENCY_K = 0.70
_CONSISTENCY_CAP = 0.50
_CONSISTENCY_SHRINK_K = 2.0

# Peak: reward the best volume's rise above the series average. Kept as a
# DEVIATION from the series' own mean — the level form ("the best book's WA")
# correlates ~0.95 with Avg WA and would merely double-count it.
_PEAK_K = 0.30
_PEAK_CAP = 0.35

# Finale: the last volume's Ending score against the series' own mean Ending, so
# this asks "did it end better or worse than it had been ending all along" rather
# than "was the ending good" (which is just Avg WA again). Capped asymmetrically:
# a botched ending damages a series more than a great one redeems it.
_FINALE_K = 0.15
_FINALE_CAP_UP = 0.30
_FINALE_CAP_DOWN = 0.50

# Total headroom for the three quality terms combined, so no series is carried
# (or buried) by structure alone — Avg WA still sets the broad shape.
_QUALITY_CLAMP = 0.75

# Insufficient evidence: a ONE-book "series" has no within-series information at
# all — no spread, no ordering, nothing to be consistent about. It is held back
# rather than allowed to rank on a single book's WA. Sits OUTSIDE the quality
# clamp because it is an evidence guard, not a judgement about the series.
# n>=2 is not penalised: the n/(n+K) shrinkage above already discounts thin
# evidence smoothly, which is a better instrument than a cliff at n=3.
_MIN_EVIDENCE_N = 2
_INSUFFICIENT_EVIDENCE_PENALTY = 0.4


def _clamp(value, low, high):
    return max(low, min(high, value))


def library_reference(books):
    """The sorted WA of every rated book — the yardstick Consistency measures a
    series' weakest volume against. Pass to series_quality_terms/series_aggregate.
    Returns None for an empty library, which disables the term rather than
    inventing a reference."""
    if books is None or "WA" not in books:
        return None
    wa = books["WA"].astype(float).dropna().to_numpy()
    return np.sort(wa) if len(wa) else None


def _weakest_percentile(min_wa, library_wa):
    """Share of the reader's rated books that the series' worst volume beats."""
    idx = int(np.searchsorted(library_wa, min_wa, side="left"))
    return idx / len(library_wa)


def _evidence_penalty(n):
    """Held-back amount for a series too thin to judge (currently only n=1)."""
    return _INSUFFICIENT_EVIDENCE_PENALTY if n < _MIN_EVIDENCE_N else 0.0


def series_quality_terms(sub, complete=False, library_wa=None):
    """Compute the per-series modifiers for one series' books.

    `sub` is the series' rows (any order — this sorts by "Series #" itself).
    `complete` says whether the reader has marked the series finished, which is
    what licenses the Finale term. `library_wa` is the sorted WA of every rated
    book (see library_reference); without it Consistency is 0, because a
    percentile has no meaning without the distribution behind it — the same
    fail-quiet rule the Finale term follows for an unmarked series.

    Returns the terms plus the raw quantities they were computed from, so the UI
    can explain the score rather than just assert it. A one-book series scores
    zero on all three terms — no spread, no ordering — and takes the
    insufficient-evidence penalty instead.
    """
    n = int(len(sub))
    wa = sub["WA"].astype(float)
    avg_wa = float(wa.mean())

    peak_lift = float(wa.max()) - avg_wa if n >= 2 else 0.0

    # Consistency: how deep into the reader's library the WEAKEST volume sits.
    weakest_pct = None
    consistency = 0.0
    if n >= 2 and library_wa is not None and len(library_wa):
        weakest_pct = _weakest_percentile(float(wa.min()), library_wa)
        shrink = n / (n + _CONSISTENCY_SHRINK_K)
        consistency = _clamp(_CONSISTENCY_K * shrink * ((weakest_pct - 0.5) * 2.0),
                             -_CONSISTENCY_CAP, _CONSISTENCY_CAP)

    # Finale: only for a finished, multi-book series whose volumes can be
    # ordered and whose last volume actually carries an Ending score.
    finale_lift = 0.0
    if complete and n >= 2 and "Ending" in sub and "Series #" in sub:
        ordered = sub.sort_values("Series #", kind="mergesort")
        endings = ordered["Ending"].astype(float)
        last = endings.iloc[-1]
        if pd.notna(last) and endings.notna().any():
            finale_lift = float(last - endings.mean())

    peak = _clamp(_PEAK_K * peak_lift, 0.0, _PEAK_CAP)
    finale = _clamp(_FINALE_K * finale_lift, -_FINALE_CAP_DOWN, _FINALE_CAP_UP)
    quality = _clamp(consistency + peak + finale, -_QUALITY_CLAMP, _QUALITY_CLAMP)

    return {
        "Consistency": consistency,
        "Peak": peak,
        "Finale": finale,
        "Quality": quality,
        # Negated to a contribution; `or 0.0` keeps it a clean 0.0 rather than
        # -0.0, which would render as "−0.000" in the UI.
        "Evidence": -_evidence_penalty(n) or 0.0,
        "Weakest Pct": weakest_pct,
        "Peak Lift": peak_lift,
        "Finale Lift": finale_lift,
        "Complete": bool(complete),
    }


_TERM_KEYS = ("Consistency", "Peak", "Finale", "Quality", "Evidence",
              "Weakest Pct", "Peak Lift", "Finale Lift", "Complete")


def _series_adjusted_wa(avg_wa, n, terms=None):
    """The series score: `avg WA + Quality + Evidence`. Without `terms` only the
    evidence guard can be applied, which is all a caller holding nothing but a
    mean and a count can honestly compute."""
    if terms is None:
        return avg_wa - _evidence_penalty(n)
    return avg_wa + terms["Quality"] + terms["Evidence"]


def series_aggregate(books, series_meta=None):
    """Aggregate rated books by series and rank them best-first by the series
    score (see the model notes above). Standalones are excluded.

    `series_meta` is the optional {series_name: {"complete": bool}} map from
    db_write.get_series_meta. When it is None every series is treated as unfinished,
    which suppresses the Finale term everywhere — deliberately, so a caller that
    cannot supply the flags never invents an ending for a series that has none.

    Consistency is measured against the WHOLE frame passed in, so a series is
    judged relative to everything the reader has rated, not just other series.

    The returned frame carries each modifier as its own column, so the score is
    auditable: Avg WA + Consistency + Peak + Finale + Evidence reconstructs
    Adjusted WA exactly, up to the shared Quality clamp.
    """
    meta = series_meta or {}
    bt = add_total_average(books)
    library_wa = library_reference(bt)
    rows = []
    for series, sub in bt.groupby("Series"):
        if (series or "").strip().lower() in _NON_SERIES:
            continue
        n = int(len(sub))
        avg_wa = float(sub["WA"].mean())
        terms = series_quality_terms(
            sub, complete=bool(meta.get(series, {}).get("complete")),
            library_wa=library_wa)
        rows.append({
            "Series": series,
            "Author": sub["Author"].mode().iloc[0] if n else "",
            "Genre": sub["Genre"].mode().iloc[0] if n else "",
            "Books": n,
            "Avg Total Average": float(sub["Total Average"].mean()),
            "Avg WA": avg_wa,
            "Adjusted WA": _series_adjusted_wa(avg_wa, n, terms),
            **{k: terms[k] for k in _TERM_KEYS},
        })
    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values("Adjusted WA", ascending=False).reset_index(drop=True)
        out.insert(0, "Rank", range(1, len(out) + 1))
    return out


# ---------------------------------------------------------------------------
# Reading stats — the BookTracker summary + genre/author rollups.
# ---------------------------------------------------------------------------
def reading_stats(books):
    """Return a dict of display-ready stat frames/scalars, all computed live:
      summary   : overall totals + per-year counts and averages.
      by_genre  : count + average WA/Total Average per genre.
      by_author : count + average WA per author.
    """
    bt = add_total_average(books)

    summary = {
        "total_books": int(len(bt)),
        "avg_wa": float(bt["WA"].mean()),
        "avg_total_average": float(bt["Total Average"].mean()),
        "avg_words": float(bt["Words"].dropna().mean()) if bt["Words"].notna().any()
        else float("nan"),
    }
    per_year = (bt.dropna(subset=["Year"])
                  .groupby("Year")
                  .agg(Books=("Book", "count"),
                       **{"Avg WA": ("WA", "mean"),
                          "Avg Total Average": ("Total Average", "mean"),
                          "Avg Words": ("Words", "mean")})
                  .reset_index()
                  .sort_values("Year"))
    per_year["Year"] = per_year["Year"].astype(int)

    by_genre = (bt.groupby("Genre")
                  .agg(Books=("Book", "count"),
                       **{"Avg WA": ("WA", "mean"),
                          "Avg Total Average": ("Total Average", "mean"),
                          "Avg Words": ("Words", "mean")})
                  .reset_index()
                  .sort_values("Avg WA", ascending=False))

    by_author = (bt.groupby("Author")
                   .agg(Books=("Book", "count"),
                        **{"Avg WA": ("WA", "mean")})
                   .reset_index()
                   .sort_values(["Books", "Avg WA"], ascending=[False, False]))

    return {"summary": summary, "per_year": per_year,
            "by_genre": by_genre, "by_author": by_author}


# ---------------------------------------------------------------------------
# Timeline — per-year books, avg WA, and the five category averages.
# ---------------------------------------------------------------------------
def timeline(books):
    """Per-year frame: book count, average WA, and the five plain category
    averages (Story/Character/Aesthetics/Theme/Worldbuilding) so reading/rating
    drift year to year is visible. Computed live from components."""
    cat_comps = _category_components(books)
    bt = books.dropna(subset=["Year"]).copy()
    rows = []
    for year, sub in bt.groupby("Year"):
        rec = {"Year": int(year), "Books": int(len(sub)),
               "Avg WA": float(sub["WA"].mean())}
        for cat in CATEGORY_ORDER:
            means = sub.apply(lambda r: category_average(r, cat, cat_comps), axis=1)
            rec[cat] = float(means.mean()) if means.notna().any() else float("nan")
        rec["Avg Words"] = (float(sub["Words"].dropna().mean())
                            if sub["Words"].notna().any() else float("nan"))
        rows.append(rec)
    return pd.DataFrame(rows).sort_values("Year").reset_index(drop=True)
