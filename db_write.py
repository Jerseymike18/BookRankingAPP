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

# Reading-log status values. 'finished' is the default for every rated book
# (set as the column default in the schema); the other two are transient states
# for the Reading Status view.
VALID_STATUSES = ("finished", "currently-reading", "reading-next")


# Map component name → SQL column suffix (spaces/hyphens → underscores)
def _col(comp: str) -> str:
    return comp.replace(" ", "_").replace("-", "_")


# ---------------------------------------------------------------------------
# Schema migration: series_number column (created once on first import)
# ---------------------------------------------------------------------------
def _ensure_series_number():
    """Add series_number INTEGER column to books and recommendations if absent."""
    con = _connect()
    for tbl in ("books", "recommendations"):
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({tbl})")}
        if "series_number" not in cols:
            con.execute(f"ALTER TABLE {tbl} ADD COLUMN series_number INTEGER")
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# Schema migration: delta_log table (created once on first import)
# ---------------------------------------------------------------------------
def _ensure_delta_log():
    """Create the delta_log table if it doesn't exist yet."""
    comp_cols = []
    for prefix in ("pred_", "act_", "d_"):
        for c in FICTION_COMPONENTS:
            comp_cols.append(f'"{prefix}{_col(c)}" REAL')
    col_ddl = ",\n    ".join(comp_cols)
    ddl = f"""
    CREATE TABLE IF NOT EXISTS delta_log (
        id         INTEGER PRIMARY KEY,
        title      TEXT NOT NULL,
        logged_at  TEXT NOT NULL,
        pred_wa    REAL,
        act_wa     REAL,
        d_wa       REAL,
        {col_ddl}
    )"""
    con = _connect()
    con.execute(ddl)
    con.commit()
    con.close()


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


_ensure_series_number()
_ensure_delta_log()


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
def add_book(title, genre, author, scores, series=None, series_number=None,
             words=None, year_read=None, allow_new_genre=False):
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
        cols = ["title", "genre", "author", "series", "series_number", "words", "year_read"] + FICTION_COMPONENTS
        vals = [title, genre, author, series, int(series_number) if series_number else None, words, year_read] + \
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
# WRITE: add a researched recommendation
# ---------------------------------------------------------------------------
def add_recommendation(title, genre, author, scores, series=None, series_number=None,
                       words=None, blurb=None, keywords=None, done=0,
                       allow_new_genre=False, require_scores=True):
    """
    Add a researched (not-yet-read) book to the recommendations table. Same
    validation discipline as add_book: genre must be known, the 14 component
    scores are range/completeness checked, and nothing commits on failure.
    Pass require_scores=False to allow adding without component scores (e.g.
    when bulk-adding series books that haven't been researched yet).
    Returns True on success, False otherwise.
    """
    con = _connect()
    try:
        valid = _valid_genres(con)
        if genre not in valid and not allow_new_genre:
            raise ValidationError(
                f"Genre '{genre}' is not in genre_weights. Either fix the "
                f"spelling (valid genres: {sorted(valid)}) or pass "
                f"allow_new_genre=True and add weights for it.")
        dup = con.execute("SELECT 1 FROM recommendations WHERE title=?",
                          (title,)).fetchone()
        if dup:
            raise ValidationError(
                f"A recommendation titled '{title}' already exists.")
        _validate_scores(scores, require_all=require_scores)

        _backup_once()
        cols = (["title", "genre", "author", "series", "series_number", "words",
                 "done", "blurb", "keywords"] + FICTION_COMPONENTS)
        vals = ([title, genre, author, series,
                 int(series_number) if series_number else None,
                 words, 1 if done else 0,
                 blurb, keywords] + [scores.get(c) for c in FICTION_COMPONENTS])
        ph = ",".join("?" for _ in cols)
        con.execute(f'INSERT INTO recommendations '
                    f'({",".join(chr(34)+c+chr(34) for c in cols)}) '
                    f'VALUES ({ph})', vals)
        con.commit()
        print(f"  ✓ Saved '{title}' to recommendations ({genre}, {author}).")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ NOT saved — {e}")
        return False
    finally:
        con.close()


# ---------------------------------------------------------------------------
# WRITE: set a recommendation's blurb / keywords (no score change)
# ---------------------------------------------------------------------------
def set_recommendation_meta(title, blurb=None, keywords=None):
    """Update only the blurb/keywords on an existing recommendation — used to
    backfill books that were added without going through research. Touches no
    component scores and no schema. Returns True on success, False otherwise."""
    con = _connect()
    try:
        row = con.execute("SELECT 1 FROM recommendations WHERE title=?",
                          (title,)).fetchone()
        if not row:
            raise ValidationError(f"No recommendation titled '{title}'.")
        _backup_once()
        con.execute("UPDATE recommendations SET blurb=?, keywords=? WHERE title=?",
                    (blurb, keywords, title))
        con.commit()
        print(f"  ✓ Updated blurb/keywords for '{title}'.")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ {e}")
        return False
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


def delete_book(title):
    """Permanently delete a rated book by title. Backs up before writing."""
    con = _connect()
    try:
        row = con.execute("SELECT id FROM books WHERE title=?", (title,)).fetchone()
        if not row:
            raise ValidationError(f"No book titled '{title}' found.")
        _backup_once()
        con.execute("DELETE FROM books WHERE title=?", (title,))
        con.commit()
        print(f"  ✓ Deleted '{title}'.")
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ Not deleted — {e}")
    finally:
        con.close()


# ---------------------------------------------------------------------------
# WRITE: reading-log status + year_read (the BookTracker port)
# ---------------------------------------------------------------------------
def delete_recommendation(title):
    """Permanently delete a TBR recommendation by title. Backs up before writing."""
    con = _connect()
    try:
        row = con.execute("SELECT id FROM recommendations WHERE title=?", (title,)).fetchone()
        if not row:
            raise ValidationError(f"No recommendation titled '{title}' found.")
        _backup_once()
        con.execute("DELETE FROM recommendations WHERE title=?", (title,))
        con.commit()
        print(f"  ✓ Deleted recommendation '{title}'.")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ Not deleted — {e}")
        return False
    finally:
        con.close()


def set_status(title, status):
    """Set a rated book's reading status (finished / currently-reading /
    reading-next). Validated against VALID_STATUSES; nothing commits on failure.
    Returns True on success, False otherwise."""
    con = _connect()
    try:
        if status not in VALID_STATUSES:
            raise ValidationError(
                f"Status '{status}' is invalid. Valid: {list(VALID_STATUSES)}.")
        row = con.execute("SELECT 1 FROM books WHERE title=?", (title,)).fetchone()
        if not row:
            raise ValidationError(f"No book titled '{title}' found.")
        _backup_once()
        con.execute("UPDATE books SET status=? WHERE title=?", (status, title))
        con.commit()
        print(f"  ✓ '{title}' status set to {status}.")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ {e}")
        return False
    finally:
        con.close()


def set_series_number(table: str, title: str, number):
    """Set series_number on a row in 'books' or 'recommendations'.
    Accepts int or float (e.g. 0.5 for prologues, 3.5 for interstitials).
    Backs up once per session before writing. Returns True on success."""
    if table not in ("books", "recommendations"):
        raise ValidationError(f"Unknown table '{table}'. Use 'books' or 'recommendations'.")
    con = _connect()
    try:
        row = con.execute(f"SELECT 1 FROM {table} WHERE title=?", (title,)).fetchone()
        if not row:
            raise ValidationError(f"No entry titled '{title}' in {table}.")
        _backup_once()
        val = float(number) if number != int(number) else int(number)
        con.execute(f"UPDATE {table} SET series_number=? WHERE title=?", (val, title))
        con.commit()
        print(f"  ✓ {table}.series_number = {val} for '{title}'.")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ {e}")
        return False
    finally:
        con.close()


def set_year_read(title, year):
    """Set/edit the year a rated book was read. Range-checked; nothing commits on
    failure. Returns True on success, False otherwise."""
    con = _connect()
    try:
        if year is not None and not (1900 <= int(year) <= 2100):
            raise ValidationError(f"Year {year} is out of range (1900-2100).")
        row = con.execute("SELECT 1 FROM books WHERE title=?", (title,)).fetchone()
        if not row:
            raise ValidationError(f"No book titled '{title}' found.")
        _backup_once()
        con.execute("UPDATE books SET year_read=? WHERE title=?",
                    (int(year) if year is not None else None, title))
        con.commit()
        print(f"  ✓ '{title}' year_read set to {year}.")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ {e}")
        return False
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
# WRITE: record a prediction-vs-actual delta when a forecast book gets rated
# ---------------------------------------------------------------------------
def log_delta(title: str, pred_scores: dict, pred_wa: float,
              act_scores: dict, act_wa: float) -> None:
    """
    Record predicted vs actual component scores and WA delta for a book that
    previously had a stored prediction. pred_scores / act_scores are both
    dicts keyed by the canonical 14 component names. Records deltas as
    (predicted − actual). Never raises — delta logging is non-fatal.
    """
    try:
        logged_at = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        pred_cols = [f'"pred_{_col(c)}"' for c in FICTION_COMPONENTS]
        act_cols  = [f'"act_{_col(c)}"'  for c in FICTION_COMPONENTS]
        d_cols    = [f'"d_{_col(c)}"'    for c in FICTION_COMPONENTS]
        all_cols  = (["title", "logged_at", "pred_wa", "act_wa", "d_wa"]
                     + pred_cols + act_cols + d_cols)
        pred_vals = [pred_scores.get(c) for c in FICTION_COMPONENTS]
        act_vals  = [act_scores.get(c)  for c in FICTION_COMPONENTS]
        d_vals    = [
            (pred_scores.get(c) - act_scores.get(c))
            if pred_scores.get(c) is not None and act_scores.get(c) is not None
            else None
            for c in FICTION_COMPONENTS
        ]
        all_vals = (
            [title, logged_at, pred_wa, act_wa, (pred_wa - act_wa)]
            + pred_vals + act_vals + d_vals
        )
        ph = ",".join("?" for _ in all_vals)
        col_str = ",".join(all_cols)
        con = _connect()
        con.execute(f"INSERT INTO delta_log ({col_str}) VALUES ({ph})", all_vals)
        con.commit()
        con.close()
        print(f"  (delta logged for '{title}': d_wa={pred_wa - act_wa:+.3f})")
    except Exception as exc:
        print(f"  (delta log skipped for '{title}': {exc})")


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


# ===========================================================================
# NONFICTION  — a deliberately SEPARATE table, weights, and CRUD.
# ===========================================================================
# Everything below writes ONLY to nonfiction_books and the two nonfiction
# weight tables. It never reads or writes the fiction `books` table, and it
# never touches the fiction engine (predict_engine / db_loader / views).
# Nonfiction has three categories — Quality / Aesthetics / Theme — instead of
# fiction's five, and shares some component NAMES (Entertainment, Prose,
# Insights, Thought-Provokingness) that live in a different table and roll up
# into different categories. Keep them separate.
#
# Derived columns: per the schema decision, nonfiction_books STORES the three
# category averages + Total Average as a cache, and they are RECOMPUTED from the
# raw components on every write (simple unweighted means; Total Average is the
# mean of the category averages, exactly as the workbook computed it) so they
# can never desync. WA is left NULL: it needs nonfiction genre weights, which
# don't exist yet and are owned by the separate nonfiction-engine work.

NONFICTION_COMPONENTS = [
    "Informativeness", "Argumentation", "Entertainment",    # Quality
    "Prose", "Phraseology",                                 # Aesthetics
    "Insights", "Philosophizing", "Thought-Provokingness",  # Theme
]

# Which raw components roll into each category average.
NONFICTION_CATEGORIES = {
    "Quality":    ["Informativeness", "Argumentation", "Entertainment"],
    "Aesthetics": ["Prose", "Phraseology"],
    "Theme":      ["Insights", "Philosophizing", "Thought-Provokingness"],
}


def _ensure_nonfiction_schema():
    """Create nonfiction_books + the two nonfiction weight tables if absent
    (idempotent; safe on every import). nonfiction_books mirrors the `books`
    conventions: REAL scores, double-quoted display-name columns, status
    default 'finished'. The weight tables mirror the fiction genre_weights /
    gcomp_weights pair but with the three nonfiction categories, and are
    created EMPTY — weights are populated by the separate nonfiction engine."""
    con = _connect()
    con.execute('''
        CREATE TABLE IF NOT EXISTS nonfiction_books (
            id          INTEGER PRIMARY KEY,
            title       TEXT NOT NULL,
            genre       TEXT,
            author      TEXT,
            series      TEXT,
            words       INTEGER,
            year_read   INTEGER,
            "Informativeness"        REAL,
            "Argumentation"          REAL,
            "Entertainment"          REAL,
            "Quality Average"        REAL,
            "Prose"                  REAL,
            "Phraseology"            REAL,
            "Aesthetics Average"     REAL,
            "Insights"               REAL,
            "Philosophizing"         REAL,
            "Thought-Provokingness"  REAL,
            "Theme Average"          REAL,
            "Total Average"          REAL,
            "WA"                     REAL,
            status         TEXT DEFAULT 'finished',
            series_number  INTEGER
        )
    ''')
    con.execute('''
        CREATE TABLE IF NOT EXISTS nonfiction_genre_weights (
            genre TEXT PRIMARY KEY,
            quality REAL, aesthetics REAL, theme REAL
        )
    ''')
    con.execute('''
        CREATE TABLE IF NOT EXISTS nonfiction_gcomp_weights (
            genre TEXT, category TEXT, component TEXT, weight REAL
        )
    ''')
    con.commit()
    con.close()


def _valid_nonfiction_genres(con):
    return {r[0] for r in con.execute("SELECT genre FROM nonfiction_genre_weights")}


def _validate_nonfiction_scores(scores, require_all=True):
    """Range-check 0-10; optionally check completeness over the 8 components."""
    for comp, v in scores.items():
        if comp not in NONFICTION_COMPONENTS:
            raise ValidationError(
                f"Unknown nonfiction component '{comp}'. Valid: {NONFICTION_COMPONENTS}")
        if v is not None and not (0 <= float(v) <= 10):
            raise ValidationError(f"{comp}={v} is out of range (must be 0-10).")
    if require_all:
        missing = [c for c in NONFICTION_COMPONENTS if scores.get(c) is None]
        if missing:
            raise ValidationError(f"Missing required component score(s): {missing}.")


def _nonfiction_averages(scores):
    """Recompute the three category averages + Total Average from component
    scores. Each category average is the unweighted mean of its present
    components; Total Average is the unweighted mean of the present category
    averages (mirrors the fiction Total Average convention and the workbook).
    Returns {avg_column_name: value_or_None}."""
    cat_avg = {}
    for cat, comps in NONFICTION_CATEGORIES.items():
        vals = [float(scores[c]) for c in comps if scores.get(c) is not None]
        cat_avg[cat] = (sum(vals) / len(vals)) if vals else None
    present = [v for v in cat_avg.values() if v is not None]
    total = (sum(present) / len(present)) if present else None
    return {
        "Quality Average":    cat_avg["Quality"],
        "Aesthetics Average": cat_avg["Aesthetics"],
        "Theme Average":      cat_avg["Theme"],
        "Total Average":      total,
    }


def add_nonfiction_book(title, author=None, genre=None, scores=None,
                        series=None, series_number=None, words=None,
                        year_read=None, status="finished",
                        allow_new_genre=False, require_scores=True):
    """Add a nonfiction book to nonfiction_books, mirroring add_book's
    discipline: duplicate title refused, scores range/completeness checked,
    nothing commits on failure. The three category averages + Total Average are
    recomputed here and stored; WA is left NULL (no nonfiction weights yet).
    genre is optional and only validated once nonfiction_genre_weights is
    populated. Returns True on success, False otherwise."""
    scores = scores or {}
    con = _connect()
    try:
        if status not in VALID_STATUSES:
            raise ValidationError(
                f"Status '{status}' is invalid. Valid: {list(VALID_STATUSES)}.")
        dup = con.execute("SELECT 1 FROM nonfiction_books WHERE title=?",
                          (title,)).fetchone()
        if dup:
            raise ValidationError(
                f"A nonfiction book titled '{title}' already exists. "
                f"Use change_nonfiction_rating() to edit it.")
        valid = _valid_nonfiction_genres(con)  # empty until weights are added
        if genre is not None and valid and genre not in valid and not allow_new_genre:
            raise ValidationError(
                f"Genre '{genre}' is not in nonfiction_genre_weights "
                f"(valid: {sorted(valid)}). Pass allow_new_genre=True to override.")
        _validate_nonfiction_scores(scores, require_all=require_scores)

        _backup_once()
        avgs = _nonfiction_averages(scores)
        cols = (["title", "genre", "author", "series", "series_number",
                 "words", "year_read", "status"]
                + NONFICTION_COMPONENTS
                + ["Quality Average", "Aesthetics Average",
                   "Theme Average", "Total Average"])
        vals = ([title, genre, author, series,
                 int(series_number) if series_number else None,
                 words, year_read, status]
                + [scores.get(c) for c in NONFICTION_COMPONENTS]
                + [avgs["Quality Average"], avgs["Aesthetics Average"],
                   avgs["Theme Average"], avgs["Total Average"]])
        ph = ",".join("?" for _ in cols)
        con.execute(f'INSERT INTO nonfiction_books '
                    f'({",".join(chr(34)+c+chr(34) for c in cols)}) '
                    f'VALUES ({ph})', vals)
        con.commit()
        ta = avgs["Total Average"]
        extra = f" — Total Average {ta:.4f}" if ta is not None else ""
        print(f"  ✓ Added nonfiction '{title}' ({author or '—'}).{extra}")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ NOT added — {e}")
        return False
    finally:
        con.close()


def change_nonfiction_rating(title, new_scores):
    """Update one or more component scores on a nonfiction book, then recompute
    and store the category averages + Total Average so they stay consistent.
    WA is not touched (stays NULL until the nonfiction engine owns it)."""
    con = _connect()
    try:
        row = con.execute("SELECT 1 FROM nonfiction_books WHERE title=?",
                          (title,)).fetchone()
        if not row:
            raise ValidationError(f"No nonfiction book titled '{title}' found.")
        _validate_nonfiction_scores(new_scores, require_all=False)

        _backup_once()
        sets = ",".join(f'"{c}"=?' for c in new_scores)
        con.execute(f"UPDATE nonfiction_books SET {sets} WHERE title=?",
                    list(new_scores.values()) + [title])
        # recompute averages from the full, now-updated component row
        cur = con.execute(
            f'SELECT {",".join(chr(34)+c+chr(34) for c in NONFICTION_COMPONENTS)} '
            f'FROM nonfiction_books WHERE title=?', (title,))
        full = dict(zip(NONFICTION_COMPONENTS, cur.fetchone()))
        avgs = _nonfiction_averages(full)
        con.execute('UPDATE nonfiction_books SET "Quality Average"=?, '
                    '"Aesthetics Average"=?, "Theme Average"=?, '
                    '"Total Average"=? WHERE title=?',
                    [avgs["Quality Average"], avgs["Aesthetics Average"],
                     avgs["Theme Average"], avgs["Total Average"], title])
        con.commit()
        changed = ", ".join(f"{c}={v}" for c, v in new_scores.items())
        print(f"  ✓ Updated nonfiction '{title}': {changed}")
        if avgs["Total Average"] is not None:
            print(f"    -> Total Average {avgs['Total Average']:.4f}")
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ Not updated — {e}")
    finally:
        con.close()


def delete_nonfiction_book(title):
    """Permanently delete a nonfiction book by title. Backs up before writing."""
    con = _connect()
    try:
        row = con.execute("SELECT 1 FROM nonfiction_books WHERE title=?",
                          (title,)).fetchone()
        if not row:
            raise ValidationError(f"No nonfiction book titled '{title}' found.")
        _backup_once()
        con.execute("DELETE FROM nonfiction_books WHERE title=?", (title,))
        con.commit()
        print(f"  ✓ Deleted nonfiction '{title}'.")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ Not deleted — {e}")
        return False
    finally:
        con.close()


def set_nonfiction_status(title, status):
    """Set a nonfiction book's reading status (finished / currently-reading /
    reading-next). Validated against VALID_STATUSES. Returns True on success."""
    con = _connect()
    try:
        if status not in VALID_STATUSES:
            raise ValidationError(
                f"Status '{status}' is invalid. Valid: {list(VALID_STATUSES)}.")
        row = con.execute("SELECT 1 FROM nonfiction_books WHERE title=?",
                          (title,)).fetchone()
        if not row:
            raise ValidationError(f"No nonfiction book titled '{title}' found.")
        _backup_once()
        con.execute("UPDATE nonfiction_books SET status=? WHERE title=?",
                    (status, title))
        con.commit()
        print(f"  ✓ Nonfiction '{title}' status set to {status}.")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ {e}")
        return False
    finally:
        con.close()


def set_nonfiction_year_read(title, year):
    """Set/edit the year a nonfiction book was read. Range-checked 1900-2100.
    Returns True on success, False otherwise."""
    con = _connect()
    try:
        if year is not None and not (1900 <= int(year) <= 2100):
            raise ValidationError(f"Year {year} is out of range (1900-2100).")
        row = con.execute("SELECT 1 FROM nonfiction_books WHERE title=?",
                          (title,)).fetchone()
        if not row:
            raise ValidationError(f"No nonfiction book titled '{title}' found.")
        _backup_once()
        con.execute("UPDATE nonfiction_books SET year_read=? WHERE title=?",
                    (int(year) if year is not None else None, title))
        con.commit()
        print(f"  ✓ Nonfiction '{title}' year_read set to {year}.")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ {e}")
        return False
    finally:
        con.close()


def list_nonfiction_books(limit=50):
    """Quick read of nonfiction_books, sorted by Total Average."""
    con = _connect()
    rows = con.execute(
        'SELECT title, author, "Total Average", status FROM nonfiction_books '
        'ORDER BY "Total Average" DESC LIMIT ?', (limit,)).fetchall()
    for t, a, ta, st in rows:
        ta_s = f"{ta:.4f}" if ta is not None else "  —   "
        print(f"  {(t or '')[:34]:<34} {(a or '')[:20]:<20} TA={ta_s}  {st}")
    con.close()


# Create the nonfiction tables on import (idempotent), same discipline as the
# fiction schema-ensure calls near the top of this module.
_ensure_nonfiction_schema()


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
