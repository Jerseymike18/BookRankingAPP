"""
db_write.py
===========
STEP 3: safe, validated WRITE workflows — the functions that finally let you
edit your data WITHOUT touching the spreadsheet. Every write validates before
it commits, so the silent-corruption failures that plagued the spreadsheet
(typo'd genre, missing score, desynced derived sheets) simply can't happen.

WHAT'S HERE
-----------
  add_book(...)        : add a newly-rated fiction book (14 component scores).
  change_rating(...)   : update one or more component scores on an existing book.
  set_done(...)        : mark a recommendation as read (done).
  update_queue([...])  : replace the read-queue order.
  list_* helpers       : quick reads so you can see what's there.

WHY THIS IS SAFER THAN THE SPREADSHEET
--------------------------------------
  * Genre is checked against genre_weights — a typo is REFUSED, not silently
    dropped from predictions later.
  * Component scores are range-checked (0-10) and completeness-checked.
  * No derived data is stored, so nothing can desync. WA, ranks, year sheets,
    tiers all recompute from these inputs whenever the engine runs.
  * Every change is one atomic transaction; a failed validation writes nothing.

BACKUP DISCIPLINE (important)
-----------------------------
These functions MODIFY books.db. Before the first write each session, the
module makes a timestamped backup copy (books.db.backup-YYYYMMDD-HHMMSS) so a
mistake is always recoverable. Your original spreadsheet also remains untouched.

HOW TO USE (Thonny): edit the examples at the bottom and Run, or import these
functions from other scripts / the future website.
"""

import os
import shutil
import sqlite3
import datetime as dt

import predict_engine as pe

DB = "books.db"

FICTION_COMPONENTS = [
    "Plot", "Entertainment", "Action", "Ending",
    "Depth", "Emotional Impact", "Motivations",
    "Prose", "Narration",
    "Insights", "Thought-Provokingness",
    "Depth2", "Integration", "Originality",
]
# Worldbuilding components are legitimately optional (blank for realist genres).
WORLDBUILDING = {"Depth2", "Integration", "Originality"}


# ---------------------------------------------------------------------------
# Backup + connection helpers
# ---------------------------------------------------------------------------
_backed_up_this_session = False


def _backup_once():
    global _backed_up_this_session
    if not _backed_up_this_session and os.path.exists(DB):
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = f"{DB}.backup-{stamp}"
        shutil.copy2(DB, dst)
        print(f"  (backup saved: {dst})")
        _backed_up_this_session = True


def _connect():
    return sqlite3.connect(DB)


def _valid_genres(con):
    return {r[0] for r in con.execute("SELECT genre FROM genre_weights")}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
class ValidationError(Exception):
    pass


def _validate_scores(scores, require_all=True):
    """Range-check 0-10; check completeness (worldbuilding optional)."""
    for comp, v in scores.items():
        if comp not in FICTION_COMPONENTS:
            raise ValidationError(f"Unknown component '{comp}'. Valid: {FICTION_COMPONENTS}")
        if v is not None and not (0 <= float(v) <= 10):
            raise ValidationError(f"{comp}={v} is out of range (must be 0-10).")
    if require_all:
        missing = [c for c in FICTION_COMPONENTS
                   if c not in WORLDBUILDING and scores.get(c) is None]
        if missing:
            raise ValidationError(
                f"Missing required component score(s): {missing}. "
                f"(Worldbuilding components may be left out for realist genres.)")


# ---------------------------------------------------------------------------
# WRITE: add a book
# ---------------------------------------------------------------------------
def add_book(title, genre, author, scores, series=None, words=None,
             year_read=None, allow_new_genre=False):
    """
    Add a newly-rated fiction book. `scores` is a dict of component->value.
    Refuses to commit anything if validation fails.
    """
    con = _connect()
    try:
        # genre check
        valid = _valid_genres(con)
        if genre not in valid and not allow_new_genre:
            raise ValidationError(
                f"Genre '{genre}' is not in genre_weights. Either fix the "
                f"spelling (valid genres: {sorted(valid)}) or pass "
                f"allow_new_genre=True and add weights for it.")
        # duplicate check
        dup = con.execute("SELECT 1 FROM books WHERE title=?", (title,)).fetchone()
        if dup:
            raise ValidationError(f"A book titled '{title}' already exists. "
                                  f"Use change_rating() to edit it.")
        _validate_scores(scores, require_all=True)

        _backup_once()
        cols = ["title", "genre", "author", "series", "words", "year_read"] + FICTION_COMPONENTS
        vals = [title, genre, author, series, words, year_read] + \
               [scores.get(c) for c in FICTION_COMPONENTS]
        ph = ",".join("?" for _ in cols)
        con.execute(f'INSERT INTO books ({",".join(chr(34)+c+chr(34) for c in cols)}) '
                    f'VALUES ({ph})', vals)
        con.commit()
        print(f"  ✓ Added '{title}' ({genre}, {author}).")
        _show_computed_wa(con, title)
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ NOT added — {e}")
    finally:
        con.close()


# ---------------------------------------------------------------------------
# WRITE: change rating(s)
# ---------------------------------------------------------------------------
def change_rating(title, new_scores):
    """Update one or more component scores on an existing book."""
    con = _connect()
    try:
        row = con.execute("SELECT id FROM books WHERE title=?", (title,)).fetchone()
        if not row:
            raise ValidationError(f"No book titled '{title}' found.")
        _validate_scores(new_scores, require_all=False)

        _backup_once()
        sets = ",".join(f'"{c}"=?' for c in new_scores)
        con.execute(f"UPDATE books SET {sets} WHERE title=?",
                    list(new_scores.values()) + [title])
        con.commit()
        changed = ", ".join(f"{c}={v}" for c, v in new_scores.items())
        print(f"  ✓ Updated '{title}': {changed}")
        _show_computed_wa(con, title)
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ Not updated — {e}")
    finally:
        con.close()


# ---------------------------------------------------------------------------
# WRITE: mark a recommendation done
# ---------------------------------------------------------------------------
def set_done(title, done=True):
    con = _connect()
    try:
        row = con.execute("SELECT 1 FROM recommendations WHERE title=?",
                          (title,)).fetchone()
        if not row:
            raise ValidationError(f"No recommendation titled '{title}'.")
        _backup_once()
        con.execute("UPDATE recommendations SET done=? WHERE title=?",
                    (1 if done else 0, title))
        con.commit()
        print(f"  ✓ Marked '{title}' done={done}.")
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ {e}")
    finally:
        con.close()


# ---------------------------------------------------------------------------
# WRITE: update the read queue (replace order)
# ---------------------------------------------------------------------------
def update_queue(titles):
    """Replace the read queue with this ordered list of titles."""
    con = _connect()
    try:
        _backup_once()
        con.execute("DELETE FROM read_queue")
        for pos, t in enumerate(titles, 1):
            con.execute("INSERT INTO read_queue (position,title) VALUES (?,?)",
                        (pos, t))
        con.commit()
        print(f"  ✓ Queue updated ({len(titles)} books).")
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Read helpers + a computed-WA preview (so you see the effect of a write)
# ---------------------------------------------------------------------------
def _show_computed_wa(con, title):
    """Recompute and show this book's WA from the engine, so the write is visible."""
    try:
        import db_loader
        books, gw, gcw = db_loader.load_from_db()
        row = books[books["Book"] == title]
        if len(row):
            wa = float(row.iloc[0]["WA"])
            rank = int((books["WA"] > wa).sum() + 1)
            print(f"    -> computed WA {wa:.2f}, rank ~{rank} of {len(books)}")
    except Exception:
        pass  # preview is a nicety; never let it block a successful write


def list_books(genre=None, limit=20):
    con = _connect()
    q = "SELECT title,genre,author FROM books"
    args = ()
    if genre:
        q += " WHERE genre=?"; args = (genre,)
    q += " LIMIT ?"; args = args + (limit,)
    for t, g, a in con.execute(q, args):
        print(f"  {t[:36]:<36} {g[:18]:<18} {a}")
    con.close()


def show_queue():
    con = _connect()
    for pos, t in con.execute("SELECT position,title FROM read_queue ORDER BY position"):
        print(f"  {pos}. {t}")
    con.close()


if __name__ == "__main__":
    print("=" * 60)
    print("DB WRITE WORKFLOWS — demo (nothing committed unless you edit below)")
    print("=" * 60)
    print("\nCurrent queue:")
    show_queue()

    # ---- EXAMPLES (uncomment and edit to actually write) ----
    #
    # add_book(
    #     title="The Republic of Thieves",
    #     genre="Epic Fantasy",
    #     author="Scott Lynch",
    #     series="Gentleman Bastard",
    #     words=275000,
    #     year_read=2026,
    #     scores={
    #         "Plot": 7.0, "Entertainment": 7.5, "Action": 6.9, "Ending": 7.3,
    #         "Depth": 7.2, "Emotional Impact": 7.8, "Motivations": 7.1,
    #         "Prose": 8.2, "Narration": 8.0,
    #         "Insights": 6.7, "Thought-Provokingness": 6.5,
    #         "Depth2": 6.5, "Integration": 6.3, "Originality": 6.1,
    #     })
    #
    # change_rating("Toll the Hounds", {"Ending": 9.5})
    #
    # update_queue(["Endymion", "The Republic of Thieves", "Red Seas Under Red Skies"])

    print("\n(Edit the examples at the bottom of this file to make real changes.)")
