"""
migrate_series_number_float.py — widen series_number to DOUBLE PRECISION (Postgres)
===================================================================================
`db_write.set_series_number` and `update_book_metadata` both document fractional
series numbers ("0.5 for prologues, 3.5 for interstitials") and both normalise to
a float before writing. That works on local SQLite, whose dynamic typing stores
1.5 verbatim — but the Phase-1 migration mapped SQLite INTEGER to BIGINT, so on
Postgres the server **silently rounds**: writing 1.5 stores 2, with no error and
no warning. The documented feature has therefore never worked on the hosted app.

Found 2026-08-15 setting "A War of Gifts" to 1.5 (it came back as 2). No data was
ever corrupted — every series_number in every table is currently whole, so this
only ever silently discarded a value at write time.

This widens the four ranking tables' `series_number` to DOUBLE PRECISION:
BIGINT -> DOUBLE PRECISION is a WIDENING conversion, so no existing value can be
lost or altered, and it matches what local SQLite already stores.

NOT included: `import_staging.series_number` (INTEGER). That table is transient
Goodreads-import scratch, cleared per batch; widen it separately if a fractional
number ever needs to survive the import path.

Postgres only. SQLite needs no change — its declared type is advisory and it
already stores floats in this column.

Usage:
    python3 migrate_series_number_float.py            # PREVIEW: types + plan
    python3 migrate_series_number_float.py --apply    # run the ALTERs
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

TABLES = ["books", "recommendations", "nonfiction_books", "nonfiction_recommendations"]
TARGET = "double precision"


def load_database_url():
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("DATABASE_URL not set and not found in .env.")


def main():
    apply = "--apply" in sys.argv
    os.environ["DATABASE_URL"] = load_database_url()
    os.environ["DB_BACKEND"] = "postgres"

    import psycopg2
    con = psycopg2.connect(os.environ["DATABASE_URL"])
    con.autocommit = False
    cur = con.cursor()

    def types():
        cur.execute(
            "SELECT table_name, data_type FROM information_schema.columns "
            "WHERE column_name='series_number' AND table_schema='public' "
            "AND table_name = ANY(%s) ORDER BY table_name", (TABLES,))
        return dict(cur.fetchall())

    before = types()
    print(f"mode: {'APPLY' if apply else 'PREVIEW (no changes)'}\n")
    todo = []
    for t in TABLES:
        cur_type = before.get(t)
        if cur_type is None:
            print(f"  {t:<28} MISSING — table or column not found; skipped")
            continue
        cur.execute(f"SELECT COUNT(*), COUNT(series_number) FROM {t}")
        n, n_set = cur.fetchone()
        # A pre-existing fractional value would mean the premise is wrong.
        cur.execute(f"SELECT COUNT(*) FROM {t} WHERE series_number IS NOT NULL "
                    f"AND series_number <> FLOOR(series_number)")
        n_frac = cur.fetchone()[0]
        state = "already ok" if cur_type == TARGET else f"{cur_type} -> {TARGET}"
        print(f"  {t:<28} {state:<28} rows={n} numbered={n_set} fractional={n_frac}")
        if cur_type != TARGET:
            todo.append(t)

    if not todo:
        print("\nNothing to do — every table is already DOUBLE PRECISION.")
        con.close()
        return 0
    if not apply:
        print("\nWould run:")
        for t in todo:
            print(f"  ALTER TABLE {t} ALTER COLUMN series_number TYPE {TARGET};")
        print("\nWidening conversion — no existing value can change. Re-run with --apply.")
        con.close()
        return 0

    try:
        for t in todo:
            print(f"  altering {t}…")
            cur.execute(f"ALTER TABLE {t} ALTER COLUMN series_number TYPE {TARGET}")
        con.commit()
    except Exception:
        con.rollback()
        print("FAILED — rolled back, no column was changed.")
        raise

    after = types()
    ok = all(after.get(t) == TARGET for t in TABLES if t in before)
    print("\nafter:")
    for t in TABLES:
        if t in after:
            print(f"  {t:<28} {after[t]}")
    print("  all four widened." if ok else "  VERIFY FAILED — see above.")
    con.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
