"""
sync_corpus_from_pg.py — make the local SQLite corpus match the hosted Postgres
source of truth, through the validated db_write path (no direct SQL).

WHY THIS EXISTS
---------------
The hosted app (Supabase Postgres) is the live source of truth for the owner's
ratings; local `books.db` is a disposable analysis mirror. The walk-forward
harness runs locally, so before re-baselining we bring the local mirror current.
This script is the reproducible way to do that: check it out, run it, then run
`python3 walkforward.py --all-splits` and the committed artifacts reproduce.

WHAT IT DOES (idempotent, read-PG / write-local-only)
-----------------------------------------------------
  1. Reads Michael's fiction `books` from Postgres (DB_BACKEND=postgres).
  2. For every PG book missing from local `books`, adds it via db_write.add_book
     (+ set_read_seq to preserve the PG reading order).
  3. Reconciles the read/done flag: any local `books` row whose `recommendations`
     row still has done=0 is flipped via db_write.set_done — so a read book is
     never left done=0 (the data-lint ERROR the export gate rejects).

Needs DATABASE_URL (from .env) + psycopg2. Prints a summary; changes nothing in
Postgres. Local books.db is intentionally NOT committed (it is a PG mirror).
"""
import os

def _load_env(path=".env"):
    try:
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass

_load_env()

UID = os.environ.get("MICHAEL_USER_ID", "e3160346-91f8-4334-a099-202217b376a5")
COMPS = ["Plot", "Entertainment", "Action", "Ending", "Depth", "Emotional Impact",
         "Motivations", "Prose", "Narration", "Insights", "Thought-Provokingness",
         "Depth2", "Integration", "Originality"]
META = ["title", "genre", "author", "series", "words", "year_read",
        "read_month", "read_seq", "series_number"]


def _read_pg():
    os.environ["DB_BACKEND"] = "postgres"
    import db_backend
    cols = META + COMPS
    con = db_backend.connect()
    q = "SELECT " + ",".join('"' + c + '"' for c in cols) + " FROM books WHERE user_id=?"
    rows = [dict(zip(cols, r)) for r in con.execute(q, (UID,)).fetchall()]
    con.close()
    return rows


def main():
    pg_rows = _read_pg()
    os.environ["DB_BACKEND"] = "sqlite"
    import db_write
    import db_loader

    local = set(db_loader.load_from_db()[0]["Book"])
    added = 0
    for d in pg_rows:
        if d["title"] in local:
            continue
        db_write.add_book(
            d["title"], d["genre"], d["author"], {c: float(d[c]) for c in COMPS},
            series=d["series"], series_number=d["series_number"],
            words=d["words"], year_read=d["year_read"], read_month=d["read_month"])
        if d.get("read_seq") is not None:
            db_write.set_read_seq(d["title"], d["read_seq"])
        added += 1

    # Reconcile done-flags: a book present in `books` must not sit done=0 in recs.
    os.environ["DB_BACKEND"] = "sqlite"
    import db_backend
    con = db_backend.connect(db_write.DB)
    undone = [r[0] for r in con.execute(
        "SELECT r.title FROM recommendations r JOIN books b "
        "ON b.user_id=r.user_id AND b.title=r.title "
        "WHERE r.user_id=? AND (r.done IS NULL OR r.done=0)", (UID,)).fetchall()]
    con.close()
    for title in undone:
        db_write.set_done(title, True)

    print(f"Sync complete: {added} book(s) added, {len(undone)} done-flag(s) reconciled.")
    print(f"Local fiction count: {len(db_loader.load_from_db()[0])}")


if __name__ == "__main__":
    main()
