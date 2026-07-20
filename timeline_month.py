"""
timeline_month.py
=================
Read-only by-MONTH aggregation for the Timeline page, complementing the per-year
`views.timeline` / `nonfiction_engine.timeline`.

The engine files own all the scoring math and are off-limits; this module does
NOT reimplement any of it. It receives an already-built engine ``books``
DataFrame plus the engine's own ``category_average`` callable and the components
map it needs, and only changes the GROUPING KEY from Year to (Year, Month). Every
number it emits is produced by the same engine function the per-year view uses,
so the two views cannot disagree.

The month for each book comes from a ``month_map`` — ``{normalized-title: month}``
built from the ``read_month`` column (year-only ``year_read`` has no month, so a
book without a backfilled ``read_month`` simply doesn't appear in the monthly
breakdown; it still appears in the per-year view). Pure and side-effect-free:
no DB, no engine import — trivially unit-testable and backend-agnostic.
"""


def _norm(title):
    return (title or "").strip().lower()


def _round(v, ndigits=2):
    """JSON-safe round: NaN/None/inf → None, else rounded float."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return round(f, ndigits)


def by_month(books, month_map, category_average, total_average,
             cat_comps, category_order):
    """Per-(year, month) rollup of the important reading stats.

    books:            engine books DataFrame (needs Book / Year / WA / Words /
                      Genre + the component columns the engine callables read).
    month_map:        {normalized-title: month(1-12)} from read_month.
    category_average: the engine's own ``category_average(row, cat, cat_comps)``.
    total_average:    the engine's own ``total_average(row, cat_comps)`` (per-book
                      Total Average — the tier metric).
    cat_comps:        the engine's ``_category_components(books)`` output.
    category_order:   the engine's category list (fiction 5 / nonfiction 3).

    Every score is produced by the engine's own callables — this module only
    changes the GROUPING (to year+month) and adds plain count/sum/max rollups.
    Returns JSON-safe dicts sorted ascending by (year, month):
      {year, month, books, total_words, avg_words, avg_wa, avg_total_average,
       <cat lowercased>..., genres, authors, top_book, top_wa}
    Books with no month in month_map are omitted (they lack a read_month).
    """
    bt = books.dropna(subset=["Year"]).copy()
    if bt.empty:
        return []
    bt["_Month"] = bt["Book"].map(lambda t: month_map.get(_norm(t)))
    bt = bt[bt["_Month"].notna()]
    if bt.empty:
        return []
    has_words = "Words" in bt.columns
    has_genre = "Genre" in bt.columns
    has_author = "Author" in bt.columns

    rows = []
    for (year, month), sub in bt.groupby(["Year", "_Month"]):
        rec = {
            "year": int(year),
            "month": int(month),
            "books": int(len(sub)),
            "avg_wa": _round(sub["WA"].mean()),
            "avg_total_average": _round(
                sub.apply(lambda r: total_average(r, cat_comps), axis=1).mean()),
        }
        for cat in category_order:
            means = sub.apply(lambda r: category_average(r, cat, cat_comps), axis=1)
            rec[cat.lower()] = _round(means.mean()) if means.notna().any() else None

        # Reading volume.
        if has_words and sub["Words"].notna().any():
            w = sub["Words"].dropna()
            rec["total_words"] = int(w.sum())
            rec["avg_words"] = _round(w.mean(), 0)
        else:
            rec["total_words"] = None
            rec["avg_words"] = None

        # Diversity.
        rec["genres"] = int(sub["Genre"].dropna().nunique()) if has_genre else None
        rec["authors"] = int(sub["Author"].dropna().nunique()) if has_author else None

        # Standout: the month's highest-WA book.
        if sub["WA"].notna().any():
            top = sub.loc[sub["WA"].idxmax()]
            rec["top_book"] = top["Book"]
            rec["top_wa"] = _round(top["WA"])
        else:
            rec["top_book"] = None
            rec["top_wa"] = None

        rows.append(rec)

    rows.sort(key=lambda r: (r["year"], r["month"]))
    return rows
