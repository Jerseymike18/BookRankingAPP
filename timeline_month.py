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


def by_month(books, month_map, category_average, cat_comps, category_order):
    """Per-(year, month) rollup: book count, average WA, the category averages,
    and average words.

    books:            engine books DataFrame (needs Book / Year / WA / Words +
                      the component columns category_average reads).
    month_map:        {normalized-title: month(1-12)} from read_month.
    category_average: the engine's own ``category_average(row, cat, cat_comps)``.
    cat_comps:        the engine's ``_category_components(books)`` output.
    category_order:   the engine's category list (fiction 5 / nonfiction 3).

    Returns a list of JSON-safe dicts sorted ascending by (year, month):
      {year, month, books, avg_wa, <cat lowercased>..., avg_words}
    Books with no month in month_map are omitted (they lack a read_month).
    """
    bt = books.dropna(subset=["Year"]).copy()
    if bt.empty:
        return []
    bt["_Month"] = bt["Book"].map(lambda t: month_map.get(_norm(t)))
    bt = bt[bt["_Month"].notna()]
    if bt.empty:
        return []

    rows = []
    for (year, month), sub in bt.groupby(["Year", "_Month"]):
        rec = {
            "year": int(year),
            "month": int(month),
            "books": int(len(sub)),
            "avg_wa": _round(sub["WA"].mean()),
        }
        for cat in category_order:
            means = sub.apply(lambda r: category_average(r, cat, cat_comps), axis=1)
            rec[cat.lower()] = _round(means.mean()) if means.notna().any() else None
        # "Words" is present on the fiction frame; the nonfiction frame omits it
        # (its per-year view shows no words either), so guard the column.
        rec["avg_words"] = (
            _round(sub["Words"].dropna().mean(), 0)
            if "Words" in sub.columns and sub["Words"].notna().any() else None
        )
        rows.append(rec)

    rows.sort(key=lambda r: (r["year"], r["month"]))
    return rows
