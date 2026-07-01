"""
test_engine.py — the safety net
================================
Re-runnable correctness checks for the whole engine. Run this after ANY change
to the code or data, and it tells you, in seconds, whether you broke something.

This captures the verification logic that was previously done by hand and thrown
away. Now it's permanent: a single command that re-checks the things that must
always be true.

WHAT IT CHECKS
--------------
  1. Data loads (Excel and DB both open, both have books and components).
  2. WA reproduction: the engine's computed WA matches the stored WA for every
     book, to the penny. (Catches a broken weighting / roll-up.)
  3. Excel/DB drift (INFORMATIONAL, not a pass/fail): the DB is the live source
     of truth and the Excel workbook is import-only, so the two are SUPPOSED to
     diverge as books are added to the DB. Printed for visibility only.
  4. Prediction sanity: a prediction runs, returns a number in 0-10, with a
     confidence interval that brackets it and a sensible rank. (Catches a
     broken prediction pipeline.)
  5. Schema integrity: every genre in the data has weights; no rated book is
     missing a required (non-worldbuilding) component. (Catches bad data.)

HOW TO READ THE OUTPUT
----------------------
Every check prints PASS or FAIL. If everything is PASS, the engine is healthy.
Any FAIL points at exactly what broke, so you can fix it before it spreads.
Exit code is 0 if all pass, 1 if any fail (useful later for automation).

HOW TO RUN (Thonny): press Run.  (Or: python3 test_engine.py)
Needs: predict_engine.py, db_loader.py, and either books.db or the spreadsheet.
"""

import sys
import os
import numpy as np

import predict_engine as pe

# Track results
_results = []


def check(name, condition, detail=""):
    """Record a pass/fail with an optional detail message."""
    status = "PASS" if condition else "FAIL"
    _results.append((name, condition, detail))
    line = f"  [{status}] {name}"
    if detail:
        line += f"  — {detail}"
    print(line)
    return condition


# ---------------------------------------------------------------------------
# 1. Data loads
# ---------------------------------------------------------------------------
def test_data_loads():
    print("\n1. DATA LOADS")
    excel_ok = db_ok = False
    try:
        xb, xgw, xgcw = pe.load_everything()
        excel_ok = len(xb) > 0 and len(pe.components_of(xb)) > 0
        check("Spreadsheet loads", excel_ok,
              f"{len(xb)} books, {len(pe.components_of(xb))} components")
    except Exception as e:
        check("Spreadsheet loads", False, f"error: {e}")

    if os.path.exists("books.db"):
        try:
            import db_loader
            db, dgw, dgcw = db_loader.load_from_db()
            db_ok = len(db) > 0 and len(pe.components_of(db)) > 0
            check("Database loads", db_ok,
                  f"{len(db)} books, {len(pe.components_of(db))} components")
        except Exception as e:
            check("Database loads", False, f"error: {e}")
    else:
        check("Database loads", True, "books.db not present (skipped)")
    return excel_ok, db_ok


# ---------------------------------------------------------------------------
# 2. WA reproduction (the core math must match the stored values)
# ---------------------------------------------------------------------------
def test_wa_reproduction(source="db"):
    print(f"\n2. WA REPRODUCTION ({source})")
    if source == "db":
        # The DB is the live source the app runs on — this is the primary check.
        if not os.path.exists("books.db"):
            check("WA matches stored", True, "no DB (skipped)")
            return
        import db_loader
        books, gw, gcw = db_loader.load_from_db()
    else:
        # Excel is import-only; this secondary check confirms the importer's WA
        # roll-up stays internally consistent with the workbook's stored WA.
        books, gw, gcw = pe.load_everything()

    # Recompute each book's WA from components and compare to stored WA.
    cats = ["Story", "Character", "Theme", "Aesthetics", "Worldbuilding"]
    mismatches = 0
    worst = 0.0
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
        d = abs(wa - b["WA"])
        if d > 1e-6:
            mismatches += 1
            worst = max(worst, d)
    check(f"All WAs match stored ({source})", mismatches == 0,
          "all match" if mismatches == 0
          else f"{mismatches} mismatches, worst Δ={worst:.4f}")


# ---------------------------------------------------------------------------
# 3. Excel vs DB drift (INFORMATIONAL — not a pass/fail)
# ---------------------------------------------------------------------------
# The DB is the live source of truth; the Excel workbook is import-only. The two
# are SUPPOSED to diverge as books are added to the DB, so a mismatch here is
# expected and is NOT a failure. This prints the current drift for visibility
# only — it records no pass/fail result and never affects the exit code.
def report_source_drift():
    print("\n3. EXCEL vs DB DRIFT  (informational, not a pass/fail)")
    if not os.path.exists("books.db"):
        print("  (no books.db present — nothing to compare)")
        return
    import db_loader
    xb, xgw, xgcw = pe.load_everything()
    db, dgw, dgcw = db_loader.load_from_db()

    # Book-set difference (the DB is expected to have more / differ over time).
    only_db = sorted(set(db["Book"]) - set(xb["Book"]))
    only_excel = sorted(set(xb["Book"]) - set(db["Book"]))
    print(f"  Books: Excel {len(xb)}, DB {len(db)}  "
          f"({len(only_db)} only in DB, {len(only_excel)} only in Excel)")
    if only_db:
        print(f"    only in DB: {', '.join(only_db[:5])}"
              + (" …" if len(only_db) > 5 else ""))
    if only_excel:
        print(f"    only in Excel: {', '.join(only_excel[:5])}"
              + (" …" if len(only_excel) > 5 else ""))

    # WA drift on the books both sources share.
    x = xb.set_index("Book")["WA"]
    d = db.set_index("Book")["WA"]
    common = x.index.intersection(d.index)
    if len(common):
        diff = (x[common] - d[common]).abs()
        print(f"  Shared books: {len(common)}, "
              f"max WA Δ={diff.max():.4f}, mean WA Δ={diff.mean():.4f}")
    print("  (drift expected — DB is the source of truth, Excel is import-only)")


# ---------------------------------------------------------------------------
# 4. Prediction sanity
# ---------------------------------------------------------------------------
def test_prediction_sanity():
    print("\n4. PREDICTION SANITY")
    try:
        data = pe.build(source="db")  # the live source the app runs on
        p = pe.predict("The Wise Man's Fear", "Patrick Rothfuss", "Epic Fantasy", data)
        wa = p["wa_final"]
        lo, hi = p["ci"]
        check("Prediction in 0-10 range", 0 <= wa <= 10, f"WA={wa:.2f}")
        check("CI brackets the prediction", lo <= wa <= hi,
              f"[{lo:.2f}, {hi:.2f}]")
        check("Rank is sensible", 1 <= p["rank"] <= p["total"],
              f"rank ~{p['rank']} of {p['total']}")
    except Exception as e:
        check("Prediction runs", False, f"error: {e}")


# ---------------------------------------------------------------------------
# 5. Schema / data integrity
# ---------------------------------------------------------------------------
def test_schema_integrity():
    print("\n5. SCHEMA & DATA INTEGRITY")
    import db_loader
    books, gw, gcw = db_loader.load_from_db()
    comps = pe.components_of(books)

    # every genre present has weights
    book_genres = set(books["Genre"].unique())
    missing = book_genres - set(gw.keys())
    check("Every genre has weights", not missing,
          "all covered" if not missing else f"missing: {sorted(missing)}")

    # no rated book missing a required (non-worldbuilding) component
    wb_comps = set(books.attrs["category_components"].get("Worldbuilding", []))
    bad = []
    for _, b in books.iterrows():
        for c in comps:
            if c in wb_comps:
                continue
            if isinstance(b[c], float) and np.isnan(b[c]):
                bad.append((b["Book"], c))
                break
    check("No missing required scores", not bad,
          "all complete" if not bad
          else f"{len(bad)} incomplete, e.g. {bad[0]}")


def main():
    print("=" * 60)
    print("ENGINE TEST SUITE")
    print("=" * 60)
    test_data_loads()
    test_wa_reproduction("db")      # primary: the live source the app runs on
    test_wa_reproduction("excel")  # secondary: importer internal consistency
    report_source_drift()          # informational only — records no pass/fail
    test_prediction_sanity()
    test_schema_integrity()

    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print("\n" + "=" * 60)
    if passed == total:
        print(f"  ALL {total} CHECKS PASSED — the engine is healthy.")
    else:
        print(f"  {total - passed} of {total} CHECKS FAILED — see [FAIL] lines above.")
    print("=" * 60)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
