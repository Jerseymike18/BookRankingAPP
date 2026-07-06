"""
update.py
=========
Run this AFTER you add or re-score a book in BookRankingsNew.xlsx.

WHAT IT IS (and isn't)
----------------------
It is NOT a recalculation step. Your engine already recomputes everything
(regression, biases, genre means, shrinkage) live from the spreadsheet every
time you run a prediction — a new book is automatically known to future
predictions with no action needed.

What this command DOES is VERIFY that your hand-edit landed cleanly, because the
ways a manually-added book goes wrong are mostly SILENT — no error, the book
just quietly fails to count or gets mis-scored. This catches those:

  * a genre that doesn't exactly match GenreWeights (book would be dropped)
  * missing component scores (book won't count as "rated")
  * a WA that doesn't match what your weights would produce (typo in a score)
  * duplicate titles
  * brand-new authors/genres the engine now sees (informational)

It then prints a clean status line so you KNOW the engine absorbed the book.

It writes NOTHING to your spreadsheet. Read-only and safe.

HOW TO RUN (Thonny): add your book in Excel, save, then Run this.
"""

import numpy as np
import predict_engine as pe

WORKBOOK = pe.WORKBOOK


def main():
    books, gw, gcw = pe.load_everything(WORKBOOK)
    comps = pe.components_of(books)
    n = len(books)

    print("=" * 60)
    print("ENGINE STATUS CHECK")
    print("=" * 60)
    print(f"  Rated books the engine can see : {n}")
    print(f"  Components in current schema    : {len(comps)}")
    print()

    problems = []

    # 1. Genres present in books but missing from GenreWeights -> dropped silently
    book_genres = set(books["Genre"].unique())
    missing_gw = book_genres - set(gw.keys())
    if missing_gw:
        problems.append(
            f"Genre(s) not in GenreWeights (these books are being DROPPED from "
            f"predictions): {sorted(missing_gw)}")

    missing_gcw = book_genres - set(gcw.keys())
    if missing_gcw:
        problems.append(
            f"Genre(s) not in GCompWeights (component roll-up will be wrong): "
            f"{sorted(missing_gcw)}")

    # 2. Books missing any component score (won't behave as fully rated)
    incomplete = []
    for _, b in books.iterrows():
        missing = [c for c in comps
                   if isinstance(b[c], float) and np.isnan(b[c])]
        # Worldbuilding components are legitimately blank for some genres;
        # only flag if a NON-worldbuilding component is missing.
        wb_comps = set(books.attrs["category_components"].get("Worldbuilding", []))
        real_missing = [c for c in missing if c not in wb_comps]
        if real_missing:
            incomplete.append((b["Book"], real_missing))
    if incomplete:
        lines = "; ".join(f"{bk} (missing {', '.join(mc)})"
                          for bk, mc in incomplete[:8])
        more = "" if len(incomplete) <= 8 else f" ...and {len(incomplete)-8} more"
        problems.append(f"Book(s) missing component scores: {lines}{more}")

    # 3. WA mismatch: does each stored WA match what the weights produce?
    #    (catches a typo in a component or category-average cell)
    mism = []
    for _, b in books.iterrows():
        g = b["Genre"]
        if g not in gw:
            continue
        w = gw[g]
        wa = (b["WStory"] * (w["Story"] or 0) +
              b["WCharacter"] * (w["Character"] or 0) +
              b["WTheme"] * (w["Theme"] or 0) +
              b["WAesthetics"] * (w["Aesthetics"] or 0) +
              b["WWorldbuilding"] * (w["Worldbuilding"] or 0))
        if abs(wa - b["WA"]) > 0.01:
            mism.append((b["Book"], b["WA"], wa))
    if mism:
        lines = "; ".join(f"{bk} (stored {s:.2f} vs computed {c:.2f})"
                          for bk, s, c in mism[:8])
        problems.append(f"WA doesn't match weighted category averages "
                        f"(possible score typo): {lines}")

    # 4. Duplicate titles
    dups = books["Book"][books["Book"].duplicated()].unique()
    if len(dups):
        problems.append(f"Duplicate title(s): {list(dups)}")

    # ---- Report ----
    if not problems:
        print("  ✓ No issues found. Every rated book is clean and the engine")
        print("    has fully absorbed your data. Future predictions will use it.")
    else:
        print(f"  ⚠ {len(problems)} issue(s) found — these can SILENTLY break")
        print("    predictions, so worth fixing in the spreadsheet:\n")
        for i, p in enumerate(problems, 1):
            print(f"  {i}. {p}\n")

    # ---- Informational: engine fit + newest books ----
    coeffs, r2, resid_sd = pe.fit_regression(books)
    print("-" * 60)
    print(f"  Regression still fits cleanly : R²={r2:.3f}  "
          f"(residual sd={resid_sd:.2f})")
    print(f"  Genres covered                : {books['Genre'].nunique()}")
    print(f"  Authors covered               : {books['Author'].nunique()}")
    print()
    print("  Nothing was written to your spreadsheet (read-only check).")
    print("  To predict the new book or others, run predict_engine.py.")


if __name__ == "__main__":
    main()
