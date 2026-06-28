"""
nonfiction_engine.py
====================
The nonfiction counterpart to the fiction engine (db_loader + views +
predict_engine), kept in a SEPARATE module so the fiction engine is never
touched. It mirrors that engine's structure, adapted to nonfiction's THREE
categories (Quality / Aesthetics / Theme) instead of fiction's five.

WHAT'S HERE
-----------
  load_nonfiction_from_db()  : reconstruct a books DataFrame from nonfiction_books,
                               discovering components + their categories + weights
                               from the nonfiction weight tables (not hardcoded),
                               same defensive approach as predict_engine v2.
  total_average / category_average / add_total_average
  rank_table()               : nonfiction ranked by Total Average (the workbook's
                               nonfiction ranking — there is no nonfiction WA there).
  tier_bands()               : reuses fiction's exact tier thresholds (views.tier_bands).
  series_aggregate()         : per-series rollup (simple group means; nonfiction has
                               no series yet, and the fiction length-bonus is fiction-
                               calibrated, so it is intentionally NOT applied here).
  reading_stats() / timeline(): mirror the fiction views, over 3 categories.
  predict_nonfiction()       : mirrors the fiction predict() SHAPE (estimate
                               components -> category averages -> WA -> analog blend
                               -> CI + rank) but with n=6 it leans ENTIRELY on
                               analogs/priors — no fitted regression — and every
                               prediction is flagged low-confidence until the
                               library grows. Ranks by Total Average.
  wa_from_components() / category_averages_from_components()
                             : public roll-up helpers (used by nonfiction_research).

WEIGHTS: Total Average is the UNWEIGHTED mean of the three category averages
(skipping any category with no scored components), exactly mirroring the fiction
definition. WA is the genre-weighted average using nonfiction_genre_weights
(category lean) and nonfiction_gcomp_weights (equal within-category), seeded by
db_write.seed_nonfiction_weights. All reads only — writes go through db_write.
"""

import sqlite3
import numpy as np
import pandas as pd

import views as _fv  # fiction views — only its category-agnostic helpers are reused

DB = "books.db"

# Nonfiction's three categories, in canonical display order.
NONFICTION_CATEGORY_ORDER = ["Quality", "Aesthetics", "Theme"]
# Books carry genre=NULL today; they fall back to this default weight profile.
DEFAULT_GENRE = "Nonfiction"


# ---------------------------------------------------------------------------
# Schema discovery + weighted category averages (mirrors db_loader)
# ---------------------------------------------------------------------------
def _discover_schema_from_db(con):
    """Learn the current nonfiction components + their categories + within-category
    weights from nonfiction_gcomp_weights — never hardcoded columns."""
    category_components, gcw = {}, {}
    for genre, cat, comp, wt in con.execute(
            "SELECT genre,category,component,weight FROM nonfiction_gcomp_weights"):
        gcw.setdefault(genre, {}).setdefault(cat, {})[comp] = wt
        category_components.setdefault(cat, [])
        if comp not in category_components[cat]:
            category_components[cat].append(comp)
    return category_components, gcw


def _effective_genre(genre, weights_keyed):
    """A book's genre if it has its own weight row, else the 'Nonfiction' default.
    All six migrated books have genre=NULL, so they use the default profile."""
    if genre and genre in weights_keyed:
        return genre
    return DEFAULT_GENRE


def _weighted_cat_avg(comp_vals, genre, cat, gcw):
    """One category average: components * within-category weights, normalized by
    the weight actually used. With the seeded equal weights this is the plain mean
    of the present components, and it correctly skips missing components (so a
    partially-scored category still averages only what's there). NaN if none."""
    cw = gcw.get(genre, {}).get(cat, {})
    total, used = 0.0, 0.0
    for comp, w in cw.items():
        v = comp_vals.get(comp)
        w = w or 0
        if v is None or (isinstance(v, float) and np.isnan(v)):
            continue
        total += float(v) * float(w)
        used += float(w)
    return (total / used) if used > 0 else float("nan")


def _wa(cat_avgs, cat_weights):
    """Weighted average across categories, renormalized over the categories that
    have a (non-NaN) average. With all three present this is
    0.45*Quality + 0.20*Aesthetics + 0.35*Theme."""
    num, den = 0.0, 0.0
    for cat in NONFICTION_CATEGORY_ORDER:
        a = cat_avgs.get(cat)
        w = cat_weights.get(cat.lower()) if cat_weights else None
        if a is None or (isinstance(a, float) and np.isnan(a)) or not w:
            continue
        num += float(a) * float(w)
        den += float(w)
    return (num / den) if den > 0 else float("nan")


def load_nonfiction_from_db(path=DB):
    """Return (books_df, gw, gcw) for nonfiction, shaped like db_loader's output:
      books_df : one row per nonfiction book with raw components, the three
                 category averages (WQuality/WAesthetics/WTheme), WA, and identity.
      gw       : {genre: {"quality","aesthetics","theme"}} category weights.
      gcw      : {genre: {category: {component: weight}}} within-category weights.
    books_df.attrs['category_components'] / ['all_components'] carry the schema."""
    con = sqlite3.connect(path)
    category_components, gcw = _discover_schema_from_db(con)
    all_components = [c for comps in category_components.values() for c in comps]

    gw = {}
    for genre, q, a, t in con.execute(
            "SELECT genre,quality,aesthetics,theme FROM nonfiction_genre_weights"):
        gw[genre] = {"quality": q, "aesthetics": a, "theme": t}

    comp_cols = ",".join(f'"{c}"' for c in all_components)
    rows = con.execute(
        f'SELECT title,genre,author,series,words,year_read,status,{comp_cols} '
        f'FROM nonfiction_books').fetchall()
    con.close()

    recs = []
    for row in rows:
        title, genre, author, series, words, year_read, status = row[:7]
        comp_vals = dict(zip(all_components, row[7:]))
        eff = _effective_genre(genre, gw)
        rec = {"Book": (title or "").strip(),
               "Genre": (genre or DEFAULT_GENRE),
               "Author": (author or "Unknown"),
               "Series": (series or "").strip().strip("'\""),
               "Words": words,
               "Year": int(year_read) if year_read is not None else None,
               "Status": (status or "finished")}
        cat_avgs = {}
        for cat in NONFICTION_CATEGORY_ORDER:
            cat_avgs[cat] = _weighted_cat_avg(comp_vals, eff, cat, gcw)
            rec["W" + cat] = cat_avgs[cat]
        for c in all_components:
            v = comp_vals.get(c)
            rec[c] = float(v) if v is not None else np.nan
        rec["WA"] = _wa(cat_avgs, gw.get(eff, {}))
        recs.append(rec)

    books = pd.DataFrame(recs)
    books.attrs["category_components"] = category_components
    books.attrs["all_components"] = all_components
    return books, gw, gcw


# ---------------------------------------------------------------------------
# Views — total average / category average / tiers / rank / series / stats /
# timeline. Mirrors views.py but over the three nonfiction categories.
# ---------------------------------------------------------------------------
def _category_components(books):
    return books.attrs.get("category_components", {})


def category_average(row, cat, cat_comps):
    """Plain mean of one category's scored components (NaN if none)."""
    comps = cat_comps.get(cat, [])
    vals = [row[c] for c in comps if c in row and pd.notna(row[c])]
    return float(np.mean(vals)) if vals else float("nan")


def total_average(row, cat_comps):
    """Unweighted mean of the per-category plain means, skipping any category with
    no scored components. Mirrors the fiction definition and the workbook."""
    cat_means = []
    for cat in NONFICTION_CATEGORY_ORDER:
        comps = cat_comps.get(cat, [])
        vals = [row[c] for c in comps if c in row and pd.notna(row[c])]
        if vals:
            cat_means.append(float(np.mean(vals)))
    return float(np.mean(cat_means)) if cat_means else float("nan")


def add_total_average(books):
    """Return a copy of `books` with a 'Total Average' column attached."""
    cat_comps = _category_components(books)
    out = books.copy()
    out["Total Average"] = out.apply(lambda r: total_average(r, cat_comps), axis=1)
    return out


def tier_bands(df, score_col="Total Average", splus_threshold=9.5):
    """Tier nonfiction by Total Average. Reuses fiction's exact band logic — the
    workbook defines no nonfiction-specific thresholds (confirmed), so per the
    brief we reuse fiction's (S+ >= 9.5, then ~9/15/25/25/15/10% percentiles)."""
    return _fv.tier_bands(df, score_col=score_col, splus_threshold=splus_threshold)


def tier_counts(df_with_tier):
    return _fv.tier_counts(df_with_tier)


def rank_table(books):
    """Nonfiction ranked best-first by Total Average (the workbook's nonfiction
    ranking; there is no nonfiction WA in the workbook). WA is kept as a column
    for the weighted lens."""
    bt = add_total_average(books)
    out = bt.sort_values("Total Average", ascending=False).reset_index(drop=True)
    out.insert(0, "Rank", range(1, len(out) + 1))
    return out


_NON_SERIES = _fv._NON_SERIES


def series_aggregate(books):
    """Per-series rollup: average Total Average, average WA, and count, ranked by
    average Total Average. NOTE: the fiction length-bonus / short-series penalty is
    fiction-calibrated and is intentionally NOT applied to nonfiction; nonfiction
    also has no series yet, so this is normally empty."""
    bt = add_total_average(books)
    rows = []
    for series, sub in bt.groupby("Series"):
        if (series or "").strip().lower() in _NON_SERIES:
            continue
        n = int(len(sub))
        rows.append({
            "Series": series,
            "Author": sub["Author"].mode().iloc[0] if n else "",
            "Books": n,
            "Avg Total Average": float(sub["Total Average"].mean()),
            "Avg WA": float(sub["WA"].mean()),
        })
    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values("Avg Total Average", ascending=False).reset_index(drop=True)
        out.insert(0, "Rank", range(1, len(out) + 1))
    return out


def reading_stats(books):
    """BookTracker-style summary + per-year / per-author rollups, computed live."""
    bt = add_total_average(books)
    summary = {
        "total_books": int(len(bt)),
        "avg_wa": float(bt["WA"].mean()) if len(bt) else float("nan"),
        "avg_total_average": float(bt["Total Average"].mean()) if len(bt) else float("nan"),
        "avg_words": float(bt["Words"].dropna().mean()) if bt["Words"].notna().any()
        else float("nan"),
    }
    per_year = (bt.dropna(subset=["Year"])
                  .groupby("Year")
                  .agg(Books=("Book", "count"),
                       **{"Avg WA": ("WA", "mean"),
                          "Avg Total Average": ("Total Average", "mean")})
                  .reset_index()
                  .sort_values("Year"))
    if len(per_year):
        per_year["Year"] = per_year["Year"].astype(int)
    by_author = (bt.groupby("Author")
                   .agg(Books=("Book", "count"),
                        **{"Avg Total Average": ("Total Average", "mean"),
                           "Avg WA": ("WA", "mean")})
                   .reset_index()
                   .sort_values(["Books", "Avg Total Average"], ascending=[False, False]))
    return {"summary": summary, "per_year": per_year, "by_author": by_author}


def timeline(books):
    """Per-year frame: book count, avg WA, and the three plain category averages."""
    cat_comps = _category_components(books)
    bt = books.dropna(subset=["Year"]).copy()
    rows = []
    for year, sub in bt.groupby("Year"):
        rec = {"Year": int(year), "Books": int(len(sub)),
               "Avg WA": float(sub["WA"].mean())}
        for cat in NONFICTION_CATEGORY_ORDER:
            means = sub.apply(lambda r: category_average(r, cat, cat_comps), axis=1)
            rec[cat] = float(means.mean()) if means.notna().any() else float("nan")
        rows.append(rec)
    return pd.DataFrame(rows).sort_values("Year").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Public roll-up helpers (used by nonfiction_research to flow researched
# components through the SAME nonfiction math the rated books use).
# ---------------------------------------------------------------------------
def category_averages_from_components(scores, genre, gw, gcw):
    """{category: average} from a {component: score} dict, via the seeded weights."""
    eff = _effective_genre(genre, gw)
    return {cat: _weighted_cat_avg(scores, eff, cat, gcw)
            for cat in NONFICTION_CATEGORY_ORDER}


def wa_from_components(scores, genre, gw, gcw):
    """(WA, {category: average}) from a {component: score} dict."""
    eff = _effective_genre(genre, gw)
    cat_avgs = category_averages_from_components(scores, genre, gw, gcw)
    return _wa(cat_avgs, gw.get(eff, {})), cat_avgs


# ---------------------------------------------------------------------------
# Prediction — mirrors predict_engine.predict() SHAPE, but with n=6 it leans
# entirely on analogs/priors. NO fitted regression (6 points would overfit).
# ---------------------------------------------------------------------------
def estimate_components(books, author, genre):
    """Prior component estimates from the best available analog source: the
    author's other books if >=2, else same-genre books if >=2, else the global
    nonfiction mean. (With six distinct-author, genre=NULL books, this is almost
    always the global mean — which is exactly why predictions are low-confidence.)"""
    all_components = books.attrs["all_components"]
    by_author = books[books["Author"] == author]
    by_genre = books[books["Genre"] == genre] if genre else books.iloc[0:0]
    if len(by_author) >= 2:
        src_name, src = "author", by_author
    elif len(by_genre) >= 2:
        src_name, src = "genre", by_genre
    else:
        src_name, src = "global", books
    est = {}
    for comp in all_components:
        vals = src[comp].dropna()
        est[comp] = (float(vals.mean()) if len(vals)
                     else float(books[comp].dropna().mean()))
    return est, src_name, len(src)


def predict_nonfiction(title, author, genre, data, z=1.645):
    """Predict where an unread nonfiction book lands. Mirrors the fiction predict
    SHAPE (estimate components -> category averages -> WA -> analog blend -> CI +
    rank) but does NOT fit a regression: with n=6 it blends the component-built
    estimate with the analog-source WA mean and uses the library's WA/Total-Average
    spread for a deliberately WIDE 90% interval. Ranks by Total Average. Every
    prediction is flagged low_confidence until the library grows.

    `data` is load_nonfiction_from_db()'s (books, gw, gcw)."""
    books, gw, gcw = data
    n_books = int(len(books))
    eff = _effective_genre(genre, gw)

    est, src_name, n_src = estimate_components(books, author, genre)
    wa_est, cat_avgs = wa_from_components(est, genre, gw, gcw)
    total_avg_est = float(np.nanmean([cat_avgs[c] for c in NONFICTION_CATEGORY_ORDER]))

    # Analog WA mean: author -> genre -> global.
    analog = books[books["Author"] == author]["WA"].dropna()
    if len(analog) < 2:
        analog = (books[books["Genre"] == genre]["WA"].dropna()
                  if genre else pd.Series([], dtype=float))
    if len(analog) < 2:
        analog = books["WA"].dropna()
    analog_mean = float(analog.mean()) if len(analog) else wa_est

    trust = n_src / (n_src + 8.0)                      # tiny n -> low trust
    wa_final = trust * wa_est + (1 - trust) * analog_mean

    bt = add_total_average(books)
    wa_sd = float(books["WA"].std(ddof=1)) if n_books > 1 else 0.0
    ta_sd = float(bt["Total Average"].std(ddof=1)) if n_books > 1 else 0.0
    wa_half, ta_half = z * wa_sd, z * ta_sd

    rank = int((bt["Total Average"] > total_avg_est).sum() + 1)
    rank_lo = int((bt["Total Average"] > total_avg_est + ta_half).sum() + 1)  # best rank #
    rank_hi = int((bt["Total Average"] > total_avg_est - ta_half).sum() + 1)

    return {
        "title": title, "author": author, "genre": genre,
        "est": est, "cat_avgs": cat_avgs,
        "wa_est": wa_est, "analog_mean": analog_mean, "trust": trust,
        "wa_final": wa_final, "wa_ci": (wa_final - wa_half, wa_final + wa_half),
        "total_avg_est": total_avg_est,
        "rank": rank, "rank_range": (rank_lo, rank_hi),
        "src": src_name, "n_src": n_src, "n": n_books,
        "low_confidence": True,
        "note": (f"n={n_books} nonfiction books — leaning on "
                 f"{src_name} prior, not a fitted model. Treat as a rough, "
                 f"low-confidence estimate until the library grows."),
    }


def build(path=DB):
    """Convenience: load and return the (books, gw, gcw) tuple predict expects."""
    return load_nonfiction_from_db(path)


def report(p):
    print("=" * 64)
    print(f"NONFICTION PREDICTION  —  {p['title']}")
    print(f"            {p['author']}  |  {p['genre']}")
    print("=" * 64)
    print(f"Component estimate source: {p['src']} (n={p['n_src']})   [un-researched prior]\n")
    print("Estimated category averages:")
    for c in NONFICTION_CATEGORY_ORDER:
        print(f"   {c:<11} {p['cat_avgs'][c]:.2f}")
    print()
    print(f"  WA estimate (weighted)    : {p['wa_est']:.2f}")
    print(f"  Analog WA mean            : {p['analog_mean']:.2f}  (trust {p['trust']:.2f})")
    print(f"  PREDICTED WA              : {p['wa_final']:.2f}")
    print(f"  90% CI                    : [{p['wa_ci'][0]:.2f}, {p['wa_ci'][1]:.2f}]")
    print(f"  PREDICTED Total Average   : {p['total_avg_est']:.2f}")
    print(f"  Predicted rank (by Total) : ~{p['rank']} of {p['n']}  "
          f"(range {p['rank_range'][0]}–{p['rank_range'][1]})")
    print(f"\n  ** LOW CONFIDENCE — {p['note']} **")


if __name__ == "__main__":
    data = build()
    books = data[0]
    print(f"Loaded {len(books)} nonfiction books.")
    print(f"Components ({len(books.attrs['all_components'])}): "
          f"{', '.join(books.attrs['all_components'])}\n")
    print(rank_table(books)[["Rank", "Book", "Author", "Total Average", "WA"]]
          .to_string(index=False))
    print()
    report(predict_nonfiction("Meditations", "Marcus Aurelius", "Nonfiction", data))
