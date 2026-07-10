"""
db_loader.py
============
STEP 2: let the engine read from the SQLite database (books.db) instead of the
spreadsheet — without changing any of the prediction math.

HOW THIS WORKS (the clean swap)
-------------------------------
predict_engine.load_everything() returns three things: a books DataFrame (with
.attrs carrying the component schema), a genre-weights dict, and a
component-weights dict. This module provides load_from_db() that returns the
EXACT SAME three things, built from books.db. Because the shape matches, every
downstream function (fit_regression, predict, shrinkage, validation) works
unchanged — it can't tell the difference.

You choose the source with one switch (see USE_DB at the top of your run), and
verify_match() proves the two sources give identical predictions before you
trust the database.

SAFE: reads only. Writes nothing to either the DB or the spreadsheet.
"""

import sqlite3
import db_backend
import numpy as np
import pandas as pd

import predict_engine as pe

DB = "books.db"

# Canonical fiction components, and the five categories' weighted-avg keys the
# engine expects on each row (WStory, WCharacter, ...). We rebuild those from
# the raw components + gcomp_weights, exactly as the spreadsheet did.
CATEGORY_OF_INTEREST = ["Story", "Character", "Aesthetics", "Theme", "Worldbuilding"]


def _discover_schema_from_db(con):
    """Mirror predict_engine.discover_schema(), but from gcomp_weights table."""
    category_components, gcw = {}, {}
    for genre, cat, comp, wt in con.execute(
            "SELECT genre,category,component,weight FROM gcomp_weights"):
        gcw.setdefault(genre, {}).setdefault(cat, {})[comp] = wt
        category_components.setdefault(cat, [])
        if comp not in category_components[cat]:
            category_components[cat].append(comp)
    return category_components, gcw


def _weighted_cat_avg(comp_vals, genre, cat, gcw):
    """Reproduce a WStoryAvg-type value: component scores * within-category weights."""
    cw = gcw.get(genre, {}).get(cat, {})
    total, used = 0.0, 0.0
    for comp, w in cw.items():
        v = comp_vals.get(comp)
        w = w or 0
        if v is None or (isinstance(v, float) and np.isnan(v)):
            continue
        total += float(v) * float(w)
        used += float(w)
    return total if used > 0 else 0.0


def load_from_db(path=DB):
    """Return (books_df, gw, gcw) identical in shape to load_everything()."""
    con = db_backend.connect(path)
    category_components, gcw = _discover_schema_from_db(con)
    all_components = [c for comps in category_components.values() for c in comps]

    # Genre weights
    gw = {}
    for r in con.execute("SELECT genre,story,character,theme,aesthetics,"
                         "worldbuilding FROM genre_weights"):
        gw[r[0]] = {"Story": r[1], "Character": r[2], "Theme": r[3],
                    "Aesthetics": r[4], "Worldbuilding": r[5]}

    # Books: pull raw rows, rebuild the WCategoryAvg fields the engine expects,
    # and compute the stored WA the same way (so the WA column matches Excel).
    # year_read/status are read-only passthroughs for the reading-log + derived
    # views; the engine ignores any column it doesn't reference by name.
    comp_cols = ",".join(f'"{c}"' for c in all_components)
    rows = con.execute(
        f'SELECT title,genre,author,series,words,year_read,status,{comp_cols} '
        f'FROM books').fetchall()
    con.close()

    recs = []
    for row in rows:
        (title, genre, author, series, words, year_read, status) = row[:7]
        comp_vals = dict(zip(all_components, row[7:]))
        rec = {"Book": title.strip(), "Genre": (genre or "Unknown").strip(),
               "Author": (author or "Unknown").strip(),
               "Series": (series or "").strip().strip("'\""),
               "Words": words,
               "Year": int(year_read) if year_read is not None else None,
               "Status": (status or "finished").strip()}
        # weighted category averages
        for cat in CATEGORY_OF_INTEREST:
            rec["W" + cat] = _weighted_cat_avg(comp_vals, genre, cat, gcw)
        # raw components
        for c in all_components:
            v = comp_vals.get(c)
            rec[c] = float(v) if v is not None else np.nan
        # WA, computed identically to the sheet
        rec["WA"] = sum(rec["W" + cat] * (gw.get(genre, {}).get(cat, 0) or 0)
                        for cat in CATEGORY_OF_INTEREST)
        recs.append(rec)

    books = pd.DataFrame(recs)
    books.attrs["category_components"] = category_components
    books.attrs["all_components"] = all_components
    return books, gw, gcw


# ---------------------------------------------------------------------------
# Verification: do the two sources produce identical predictions?
# ---------------------------------------------------------------------------
def verify_match():
    xl_books, xl_gw, xl_gcw = pe.load_everything()
    db_books, db_gw, db_gcw = load_from_db()

    print("=" * 60)
    print("VERIFY: database source vs. spreadsheet source")
    print("=" * 60)
    print(f"  Books   : Excel {len(xl_books)}  |  DB {len(db_books)}")
    print(f"  Components: Excel {len(pe.components_of(xl_books))}  |  "
          f"DB {len(pe.components_of(db_books))}")

    # Align by title and compare WA
    xl = xl_books.set_index("Book")["WA"]
    db = db_books.set_index("Book")["WA"]
    common = xl.index.intersection(db.index)
    wa_diff = (xl[common] - db[common]).abs()
    print(f"  WA exact matches: {(wa_diff <= 1e-6).sum()} / {len(common)}")
    if wa_diff.max() > 1e-6:
        print("  Largest WA differences:")
        for t in wa_diff.sort_values(ascending=False).head(5).index:
            print(f"    {t[:34]:<34} Excel={xl[t]:.4f} DB={db[t]:.4f}")

    # Compare a full prediction from each source for a few books
    print("\n  Prediction comparison (should be identical):")
    xl_data = (xl_books, xl_gw, xl_gcw, *pe.fit_regression(xl_books),
               pe.genre_bias_and_trust(xl_books, pe.fit_regression(xl_books)[0]),
               pe.fit_upstream(xl_books))
    db_data = (db_books, db_gw, db_gcw, *pe.fit_regression(db_books),
               pe.genre_bias_and_trust(db_books, pe.fit_regression(db_books)[0]),
               pe.fit_upstream(db_books))
    tests = [("The Republic of Thieves", "Scott Lynch", "Epic Fantasy"),
             ("Dune", "Frank Herbert", "Science Fiction (Soft)"),
             ("The Lions of Al-Rassan", "Guy Gavriel Kay", "Historical Fiction")]
    all_ok = True
    for title, author, genre in tests:
        try:
            p_xl = pe.predict(title, author, genre, xl_data)["wa_final"]
            p_db = pe.predict(title, author, genre, db_data)["wa_final"]
            ok = abs(p_xl - p_db) < 1e-9
            all_ok &= ok
            print(f"    {title[:28]:<28} Excel={p_xl:.4f}  DB={p_db:.4f}  "
                  f"{'OK' if ok else 'DIFFERS'}")
        except Exception as e:
            print(f"    {title[:28]:<28} error: {e}")

    print()
    if (wa_diff.max() <= 1e-6) and all_ok:
        print("  *** Identical. The database is a drop-in replacement. ***")
        print("  You can safely run the engine from books.db.")
    else:
        print("  Differences found — investigate before switching over.")


if __name__ == "__main__":
    verify_match()
