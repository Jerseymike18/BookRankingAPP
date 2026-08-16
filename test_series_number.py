"""
test_series_number.py — fractional series ordinals must survive end to end
==========================================================================
Goodreads writes novella ordinals as fractions ("Edgedancer (The Stormlight
Archive, #2.5)"), and the reader's own conventions use them (0.5 prequels, 3.5
interstitials). Both `set_series_number` and `update_book_metadata` have always
documented that. It never worked outside local SQLite:

  * Postgres stored series_number as BIGINT, so a fractional write was SILENTLY
    ROUNDED by the server — 1.5 came back as 2, with no error (fixed 2026-08-15
    by migrate_series_number_float.py, which widened all five columns).
  * SIX separate `int()` coercions between the Goodreads CSV and the stored row
    threw the fraction away regardless of column type: the parser, the staging
    INSERT, the staging row->dict, the staging review-edit, and the four add_*
    writers.

Both are fixed, and this is the gate. The interesting checks are the CHAIN ones
(section 3): a unit test of the parser alone would have passed for the whole
period the feature was broken, because the value was discarded further down.

Zero API spend, no network — pure functions plus a throwaway copy of books.db.

Run:  python3 test_series_number.py     (exit 0 = pass, 1 = fail)
"""

import contextlib
import io
import os
import shutil
import sqlite3
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

_results = []


def check(name, condition, detail=""):
    _results.append(bool(condition))
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return bool(condition)


def main():
    import db_backend
    import db_write
    import goodreads_import as gi

    print("\nSERIES NUMBERS — fractional ordinals survive end to end")

    # ── 1. The parser ───────────────────────────────────────────────────────
    print("\n  parser (goodreads_import.split_series)")
    cases = [
        ("The Way of Kings (The Stormlight Archive, #1)",
         ("The Way of Kings", "The Stormlight Archive", 1)),
        ("Edgedancer (The Stormlight Archive, #2.5)",
         ("Edgedancer", "The Stormlight Archive", 2.5)),
        ("A War of Gifts (Ender Saga, #1.5)",
         ("A War of Gifts", "Ender Saga", 1.5)),
        ("Piranesi", ("Piranesi", None, None)),
        ("", ("", None, None)),
    ]
    for raw, want in cases:
        got = gi.split_series(raw)
        check(f"split_series({raw[:38]!r})", got == want, f"got {got}")
    # A whole fraction must normalise to int, not stay 1.0 — otherwise the UI
    # renders "#1.0" (seriesLabel interpolates the raw number).
    check("a whole ordinal stays an int (no '#1.0' in the UI)",
          isinstance(gi.split_series("X (S, #1)")[2], int))
    check("a fractional ordinal is a float, not rounded",
          gi.split_series("X (S, #2.5)")[2] == 2.5)

    # ── 2. The normaliser ───────────────────────────────────────────────────
    print("\n  normaliser (db_write._norm_series_number)")
    for v, want in [(1, 1), (1.0, 1), ("2", 2), (2.5, 2.5), ("3.5", 3.5),
                    (None, None), ("", None), ("  ", None), ("abc", None), (0, 0)]:
        got = db_write._norm_series_number(v)
        check(f"_norm_series_number({v!r}) -> {want!r}", got == want, f"got {got!r}")
    check("whole floats come back as int (type, not just value)",
          isinstance(db_write._norm_series_number(4.0), int))
    check("series_number is NOT in the staging int-column set",
          "series_number" not in db_write._STAGING_INT_COLS,
          f"_STAGING_INT_COLS={db_write._STAGING_INT_COLS}")

    # ── 3. The CHAIN — the checks that actually guard the bug ───────────────
    src = os.path.join(PROJECT_ROOT, "books.db")
    if not os.path.exists(src):
        check("books.db present for the chain checks", False)
        return 1
    tmpd = tempfile.mkdtemp(prefix="series_num_")
    tmpdb = os.path.join(tmpd, "books.db")
    shutil.copy2(src, tmpdb)
    orig_db = db_write.DB
    try:
        db_write.DB = tmpdb
        db_write._backed_up_this_session = True
        uid = db_backend.DEFAULT_USER_ID

        con = sqlite3.connect(tmpdb)
        genre = con.execute("SELECT genre FROM genre_weights LIMIT 1").fetchone()[0]
        con.close()

        print("\n  chain: writers preserve the fraction")
        with contextlib.redirect_stdout(io.StringIO()):
            db_write.add_recommendation("ZZ Frac Rec", genre, "Test Author", {},
                                        series="ZZ Series", series_number=2.5,
                                        require_scores=False, user_id=uid)
            db_write.add_recommendation("ZZ Whole Rec", genre, "Test Author", {},
                                        series="ZZ Series", series_number=3,
                                        require_scores=False, user_id=uid)

        def stored(title, table="recommendations"):
            con = sqlite3.connect(tmpdb)
            r = con.execute(f"SELECT series_number FROM {table} WHERE title=? AND user_id=?",
                            (title, uid)).fetchone()
            con.close()
            return r[0] if r else None

        check("add_recommendation stores 2.5 (was int()ed to 2)",
              stored("ZZ Frac Rec") == 2.5, f"stored {stored('ZZ Frac Rec')!r}")
        check("add_recommendation still stores a whole number as 3",
              stored("ZZ Whole Rec") == 3, f"stored {stored('ZZ Whole Rec')!r}")

        with contextlib.redirect_stdout(io.StringIO()):
            ok = db_write.set_series_number("recommendations", "ZZ Whole Rec", 4.5,
                                            user_id=uid)
        check("set_series_number writes 4.5 and its round-trip guard passes",
              ok and stored("ZZ Whole Rec") == 4.5, f"ok={ok} stored={stored('ZZ Whole Rec')!r}")

        with contextlib.redirect_stdout(io.StringIO()):
            rep = db_write.update_book_metadata("ZZ Frac Rec", "recommendations",
                                                {"series_number": 0.5}, user_id=uid)
        check("update_book_metadata writes 0.5",
              rep.get("ok") and stored("ZZ Frac Rec") == 0.5,
              f"stored {stored('ZZ Frac Rec')!r}")

        # The full import path: parse -> stage -> read back -> commit.
        print("\n  chain: CSV -> staging -> commit")
        title, series, num = gi.split_series("ZZ Novella (ZZ Series, #2.5)")
        with contextlib.redirect_stdout(io.StringIO()):
            res = db_write.stage_import_rows(
                uid,
                [{"kind": "fiction", "shelf": "to-read",
                  "title": title, "author": "Test Author",
                  "genre": genre, "series": series, "series_number": num,
                  "words": None, "year_read": None, "read_month": None,
                  "goodreads_rating": None, "goodreads_review": None}])
            bid = res["batch_id"]
            rows = db_write.get_staging_rows(uid, batch_id=bid)
        staged = next((r for r in rows if r["title"] == "ZZ Novella"), None)
        check("staged row keeps 2.5 (the INSERT used to int() it)",
              staged is not None and staged["series_number"] == 2.5,
              f"staged {staged and staged['series_number']!r}")

        # The SCHEMA DECLARATION itself. Every series_number column used to be
        # declared INTEGER, and migrate_sqlite_to_postgres maps INTEGER -> BIGINT
        # — which is precisely how the silent rounding reached production. They
        # are NUMERIC now (SQLite: int stays int, 2.5 stays real; the migration
        # maps NUMERIC -> DOUBLE PRECISION). Assert against a table created from
        # scratch, so a future edit that reverts the declaration fails here.
        print("\n  schema: a freshly-created table holds both")
        fresh = os.path.join(tmpd, "fresh.db")
        fcon = sqlite3.connect(fresh)
        decls = [ln.strip() for ln in open(os.path.join(PROJECT_ROOT, "db_write.py"))
                 if "series_number" in ln and ("NUMERIC" in ln or "INTEGER" in ln)
                 and "ALTER" not in ln and "#" not in ln]
        check("no series_number column is still declared INTEGER",
              not any("INTEGER" in d for d in decls), f"{decls}")
        fcon.execute("CREATE TABLE t (title TEXT, series_number NUMERIC)")
        fcon.executemany("INSERT INTO t VALUES (?,?)", [("whole", 3), ("frac", 2.5)])
        fcon.commit()
        got = dict(fcon.execute("SELECT title, series_number FROM t").fetchall())
        types = dict(fcon.execute("SELECT title, typeof(series_number) FROM t").fetchall())
        fcon.close()
        check("NUMERIC keeps a whole ordinal an int (not 3.0 in the UI)",
              got["whole"] == 3 and types["whole"] == "integer",
              f"{got['whole']!r} typeof={types['whole']}")
        check("NUMERIC keeps a fractional ordinal at 2.5",
              got["frac"] == 2.5 and types["frac"] == "real",
              f"{got['frac']!r} typeof={types['frac']}")

        with contextlib.redirect_stdout(io.StringIO()):
            db_write.update_staging_row(uid, staged["id"], {"state": "confirmed"})
            db_write.commit_staged(uid, batch_id=bid)
        check("committed recommendation keeps 2.5 end to end",
              stored("ZZ Novella") == 2.5, f"stored {stored('ZZ Novella')!r}")
    finally:
        db_write.DB = orig_db
        shutil.rmtree(tmpd, ignore_errors=True)

    n_pass = sum(_results); n = len(_results)
    print("\n" + "=" * 60)
    print(f"  ALL {n} SERIES-NUMBER CHECKS PASSED" if n_pass == n
          else f"  {n - n_pass}/{n} FAILED — a fractional ordinal is being discarded")
    print("=" * 60)
    return 0 if n_pass == n else 1


if __name__ == "__main__":
    raise SystemExit(main())
