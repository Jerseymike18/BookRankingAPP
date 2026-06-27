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
  series_aggregate(...): per-series rollup (avg Total Average, avg WA, count).
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


def _series_adjusted_wa(avg_wa, n):
    """Spreadsheet formula: avg WA + length bonus − short-series penalty.

    Bonus  = 0.0582 * (1.18^(n-1) - 1)  when n > 1, else 0
    Penalty= max(0, 3 - n) * 0.2
    """
    bonus = 0.0582 * (1.18 ** (n - 1) - 1) if n > 1 else 0.0
    penalty = max(0, 3 - n) * 0.2
    return avg_wa + bonus - penalty


def series_aggregate(books):
    """Aggregate rated books by series. For each real series, compute the average
    Total Average, the average WA, the length-adjusted WA (spreadsheet formula),
    and the book count, then rank by adjusted WA (best first). Standalones are
    excluded."""
    bt = add_total_average(books)
    rows = []
    for series, sub in bt.groupby("Series"):
        if (series or "").strip().lower() in _NON_SERIES:
            continue
        n = int(len(sub))
        avg_wa = float(sub["WA"].mean())
        rows.append({
            "Series": series,
            "Author": sub["Author"].mode().iloc[0] if n else "",
            "Genre": sub["Genre"].mode().iloc[0] if n else "",
            "Books": n,
            "Avg Total Average": float(sub["Total Average"].mean()),
            "Avg WA": avg_wa,
            "Adjusted WA": _series_adjusted_wa(avg_wa, n),
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
