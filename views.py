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
# A series is scored as `Avg WA + Commitment + Peak - Floor + Finale`.
#
# Avg WA is the base: a series of great books should rank above a series of
# mediocre ones, and that ordering is already correct. The four modifiers exist
# because a mean over books is STRUCTURALLY BLIND to everything that makes a
# series a series rather than a pile of novels — it is order-invariant and
# spread-invariant. Each term below measures something the mean cannot see:
#
#   Commitment  how long it sustained that quality        (mean ignores count)
#   Peak        whether it ever produced a standout       (mean flattens the top)
#   Floor       whether any volume was a slog             (mean hides the bottom)
#   Finale      whether it stuck the landing              (mean ignores position)
#
# Peak and Floor are deliberately ASYMMETRIC — a reward for the ceiling and a
# penalty for the collapse, not one signed spread term. Together they say "a high
# high is worth having, a bad book is worth avoiding", which is a real preference;
# a single variance term would net them out to nothing.
#
# Every term is entered as a DEVIATION FROM THE SERIES' OWN AVERAGE, never as a
# level. That is what keeps them from silently re-weighting Avg WA: measured
# against this library, the level forms ("the finale's Ending score", "the best
# book's WA") correlate 0.84-0.95 with Avg WA and would merely double-count it,
# while the deviation forms correlate -0.14 to +0.45 and carry new information.

# Commitment: the original spreadsheet length term, unchanged.
_LENGTH_BONUS_K = 0.0582
_LENGTH_BONUS_BASE = 1.18
_SHORT_SERIES_FLOOR = 3        # books below this are penalised...
_SHORT_SERIES_PENALTY = 0.2    # ...by this much per missing book

# Peak: reward the best volume's rise above the series average.
_PEAK_K = 0.30
_PEAK_CAP = 0.35

# Floor: penalise the worst volume's fall below the series average, forgiving
# the first _FLOOR_TOL of it — books in any series vary, and only a real drop
# should read as "this series has a dud in it".
_FLOOR_TOL = 0.40
_FLOOR_K = 0.25
_FLOOR_CAP = 0.45

# Finale: the last volume's Ending score against the series' own mean Ending, so
# this asks "did it end better or worse than it had been ending all along" rather
# than "was the ending good" (which is just Avg WA again). Capped asymmetrically:
# a botched ending damages a series more than a great one redeems it.
_FINALE_K = 0.15
_FINALE_CAP_UP = 0.30
_FINALE_CAP_DOWN = 0.50

# Total headroom for the three NEW terms (Peak/Floor/Finale) combined. Commitment
# is outside this budget — it is the pre-existing behaviour and is unchanged.
_QUALITY_CLAMP = 0.75


def _clamp(value, low, high):
    return max(low, min(high, value))


def _commitment_term(n):
    """The original length adjustment: a compounding bonus for a series that
    sustained itself, minus a penalty for one too short to have earned the name."""
    bonus = (_LENGTH_BONUS_K * (_LENGTH_BONUS_BASE ** (n - 1) - 1)) if n > 1 else 0.0
    penalty = max(0, _SHORT_SERIES_FLOOR - n) * _SHORT_SERIES_PENALTY
    return bonus - penalty


def series_quality_terms(sub, complete=False):
    """Compute the per-series quality modifiers for one series' books.

    `sub` is the series' rows (any order — this sorts by "Series #" itself);
    `complete` says whether the reader has marked the series as finished, which
    is what licenses the Finale term. Returns a dict of the four terms plus the
    raw deviations they were computed from, so the UI can explain the score
    rather than just assert it.

    A one-book series gets zero for all three new terms: with a single volume
    there is no spread to measure and no ordering to have a finale in. It keeps
    the Commitment term (and therefore the short-series penalty) as before.
    """
    n = int(len(sub))
    wa = sub["WA"].astype(float)
    avg_wa = float(wa.mean())

    peak_lift = float(wa.max()) - avg_wa if n >= 2 else 0.0
    floor_drop = avg_wa - float(wa.min()) if n >= 2 else 0.0

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
    floor = _clamp(_FLOOR_K * max(0.0, floor_drop - _FLOOR_TOL), 0.0, _FLOOR_CAP)
    finale = _clamp(_FINALE_K * finale_lift, -_FINALE_CAP_DOWN, _FINALE_CAP_UP)

    # The three new terms share one budget, so no single series can be carried
    # (or buried) by structure alone — Avg WA still sets the broad shape.
    quality = _clamp(peak - floor + finale, -_QUALITY_CLAMP, _QUALITY_CLAMP)

    return {
        "Commitment": _commitment_term(n),
        "Peak": peak,
        "Floor": -floor,
        "Finale": finale,
        "Quality": quality,
        "Peak Lift": peak_lift,
        "Floor Drop": floor_drop,
        "Finale Lift": finale_lift,
        "Complete": bool(complete),
    }


def _series_adjusted_wa(avg_wa, n, terms=None):
    """The series score. With `terms` (from series_quality_terms) this is
    `avg WA + Commitment + Quality`; without them it degrades to the original
    length-only adjustment, which is what a caller holding nothing but a mean
    and a count can honestly compute."""
    if terms is None:
        return avg_wa + _commitment_term(n)
    return avg_wa + terms["Commitment"] + terms["Quality"]


def series_aggregate(books, series_meta=None):
    """Aggregate rated books by series and rank them best-first by the series
    score (see the model notes above). Standalones are excluded.

    `series_meta` is the optional {series_name: {"complete": bool}} map from
    db_write.get_series_meta. When it is None every series is treated as unfinished,
    which suppresses the Finale term everywhere — deliberately, so a caller that
    cannot supply the flags never invents an ending for a series that has none.

    The returned frame carries each modifier as its own column, so the score is
    auditable: Avg WA + Commitment + Peak + Floor + Finale reconstructs Adjusted
    WA exactly, up to the shared Quality clamp.
    """
    meta = series_meta or {}
    bt = add_total_average(books)
    rows = []
    for series, sub in bt.groupby("Series"):
        if (series or "").strip().lower() in _NON_SERIES:
            continue
        n = int(len(sub))
        avg_wa = float(sub["WA"].mean())
        terms = series_quality_terms(
            sub, complete=bool(meta.get(series, {}).get("complete")))
        rows.append({
            "Series": series,
            "Author": sub["Author"].mode().iloc[0] if n else "",
            "Genre": sub["Genre"].mode().iloc[0] if n else "",
            "Books": n,
            "Avg Total Average": float(sub["Total Average"].mean()),
            "Avg WA": avg_wa,
            "Adjusted WA": _series_adjusted_wa(avg_wa, n, terms),
            **{k: terms[k] for k in ("Commitment", "Peak", "Floor", "Finale",
                                     "Quality", "Peak Lift", "Floor Drop",
                                     "Finale Lift", "Complete")},
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

# TEST ONLY — unexplained engine edit, verifying the merge block. Branch is deleted.
