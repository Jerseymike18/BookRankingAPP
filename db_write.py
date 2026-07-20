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
import json
import math
import shutil
import sqlite3
import db_backend
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

# The five scoring categories, in the same order (and spelling) the loader's
# genre-weights dict uses (db_loader.CATEGORY_OF_INTEREST). Category weights per
# genre sum to 1.0; the WA roll-up assumes that, so writes here normalize to it.
CATEGORIES = ["Story", "Character", "Theme", "Aesthetics", "Worldbuilding"]

# Fiction category → its components (fixed; mirrors CLAUDE.md's scoring model and
# the NONFICTION_CATEGORIES map below). Used to seed a brand-new user genre with
# equal within-category weights.
FICTION_CATEGORIES = {
    "Story": ["Plot", "Entertainment", "Action", "Ending"],
    "Character": ["Depth", "Emotional Impact", "Motivations"],
    "Aesthetics": ["Prose", "Narration"],
    "Theme": ["Insights", "Thought-Provokingness"],
    "Worldbuilding": ["Depth2", "Integration", "Originality"],
}

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
        cols = set(db_backend.table_columns(con, tbl))
        if "series_number" not in cols:
            con.execute(f"ALTER TABLE {tbl} ADD COLUMN series_number INTEGER")
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# Schema migration: read_month column (2026-07). Optional 1-12 month a book was
# read, complementing the year-only year_read, so the reading log can be ordered
# and bucketed by month (Delta Log chronological order + by-month Timeline). It
# is a plain nullable passthrough — the read-only engine ignores it; only the
# reading-log/timeline views read it. Self-migrating on both SQLite and Postgres
# (ALTER-if-missing at import), leaving every historical row NULL until backfilled.
# NOTE: this must be called AFTER _ensure_nonfiction_schema() so nonfiction_books
# already exists — see the call site at the bottom of this module.
# ---------------------------------------------------------------------------
def _ensure_read_month():
    """Add read_month INTEGER (1-12, nullable) to books and nonfiction_books if
    absent. Skips a table that does not exist yet (empty column set)."""
    con = _connect()
    for tbl in ("books", "nonfiction_books"):
        cols = set(db_backend.table_columns(con, tbl))
        if cols and "read_month" not in cols:
            con.execute(f"ALTER TABLE {tbl} ADD COLUMN read_month INTEGER")
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# Research model that produces predictions (Opus-era). Kept in sync with
# research_layer.MODEL — the canonical research-pipeline constant (CLAUDE.md:
# "single named constant per pipeline"). Stamped onto every live delta_log row
# so Opus-era predicted-vs-actual pairs can later be recomputed in isolation,
# without the older Sonnet-era rows (which stay NULL and are never relabeled).
# ---------------------------------------------------------------------------
RESEARCH_MODEL = "claude-opus-4-8"


# ---------------------------------------------------------------------------
# Mechanism-metadata columns on delta_log (2026-07 upgrade). Each captures ONE
# dimension of HOW a prediction was made, so residuals can later be grouped by
# mechanism (genre / author / confidence / correction magnitude / CI width),
# not just by book — turning calibration from book-level to mechanism-level.
# All nullable; the idempotent ALTER-if-missing path (mirroring how pred_model
# was added) leaves every historical row NULL and never relabels it. Populated
# going forward by log_delta(meta=...). Kept as one (name, type) list so
# _ensure_delta_log builds both the CREATE fragment and the ALTER loop from it,
# and log_delta whitelists writes against it.
#   analogs+weights : n_author / n_genre ARE the blend weights — the author layer
#                     gets n/(n+K_AUTHOR), the genre layer n/(n+K_GENRE)
#                     (reresearch_and_measure.correct_book); analog_src names the
#                     pool that fired.
#   corrections     : corr_method = which corrections ran; corr_genre/corr_author
#                     = the two hierarchical author_genre layers in WA points;
#                     corr_dtracker = the manual DeltaTracker per-component pass
#                     (Excel-only P-step — NULL on the coded path); corr_wa = net.
#   confidence/CI   : conf = model's own flag; ci_low/ci_high/ci_width at predict.
# ---------------------------------------------------------------------------
DELTA_META_COLUMNS = [
    ("pred_genre", "TEXT"), ("pred_author", "TEXT"), ("pred_words", "INTEGER"),
    ("analog_src", "TEXT"), ("n_author", "INTEGER"), ("n_genre", "INTEGER"),
    ("corr_method", "TEXT"), ("corr_genre", "REAL"), ("corr_author", "REAL"),
    ("corr_dtracker", "REAL"), ("corr_wa", "REAL"),
    ("ci_low", "REAL"), ("ci_high", "REAL"), ("ci_width", "REAL"), ("conf", "TEXT"),
    # Retro-sweep provenance (2026-07 calibration project). `tag` permanently
    # distinguishes bulk retro-sweep calibration rows (e.g. retro_sweep_v1_shrunk)
    # from genuine prospective rows so the two can never be confused and the sweep
    # can be isolated/queried/re-run idempotently on (title, tag). `analog_wa`
    # records the pure-analog leave-one-out baseline WA alongside the research
    # prediction (pred_wa) for mechanism analysis. Both nullable; the idempotent
    # ALTER-if-missing path leaves every prospective/historical row NULL.
    ("tag", "TEXT"), ("analog_wa", "REAL"),
]


# Schema migration: delta_log table (created once on first import)
# ---------------------------------------------------------------------------
def _ensure_delta_log():
    """Create the delta_log table if it doesn't exist yet, and add newer tag
    columns (pred_model, then the DELTA_META_COLUMNS mechanism-metadata block)
    on older DBs that predate them. Idempotent ALTER-if-missing; pre-existing
    rows keep every added column NULL — historical deltas are never relabeled."""
    comp_cols = []
    for prefix in ("pred_", "act_", "d_"):
        for c in FICTION_COMPONENTS:
            comp_cols.append(f'"{prefix}{_col(c)}" REAL')
    col_ddl = ",\n    ".join(comp_cols)
    meta_ddl = ",\n    ".join(f"{name} {typ}" for name, typ in DELTA_META_COLUMNS)
    ddl = f"""
    CREATE TABLE IF NOT EXISTS delta_log (
        id         INTEGER PRIMARY KEY,
        title      TEXT NOT NULL,
        logged_at  TEXT NOT NULL,
        pred_wa    REAL,
        act_wa     REAL,
        d_wa       REAL,
        {col_ddl},
        pred_model TEXT,
        {meta_ddl}
    )"""
    con = _connect()
    con.execute(ddl)
    # Back-compat: add each newer column to DBs created before it existed. Rows
    # that predate a column keep it NULL (never retroactively relabeled) — the
    # same discipline pred_model shipped with, now extended to the metadata block.
    have = db_backend.table_columns(con, "delta_log")
    for name, typ in [("pred_model", "TEXT")] + DELTA_META_COLUMNS:
        if name not in have:
            con.execute(f"ALTER TABLE delta_log ADD COLUMN {name} {typ}")
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
    return db_backend.connect(DB)


# ---------------------------------------------------------------------------
# Durable research cache (global) — survives Railway redeploys
# ---------------------------------------------------------------------------
# The LLM research caches (llm_scores_richer.json / web_grounded_cache.json) are
# title-keyed JSON files: git-committed as the warm SEED, but runtime writes land
# on the container filesystem and are LOST on every redeploy (and not shared across
# instances), so a book grounded on the live app is re-researched — a ~38-110s
# web_search — after each deploy. This global table is the DURABLE store for
# research produced at runtime. The serving path reads it ONLY on a file-cache miss
# (one cheap read right before a multi-second LLM call, so the fast hit path is
# untouched) and writes one row after researching. Global (no user_id) — the same
# book has the same grounded facts for every tenant. NEVER purged. `cache_name` is
# the source file's basename, so the base and grounded caches share one table;
# `title_key` is caller-normalized (see research_predict.db_cache_*). Portable DDL /
# UPSERT — runs on SQLite and Postgres alike.
_research_cache_ensured = False


def _ensure_research_cache():
    global _research_cache_ensured
    if _research_cache_ensured:
        return
    con = _connect()
    con.execute("""
        CREATE TABLE IF NOT EXISTS research_cache (
            cache_name TEXT NOT NULL,
            title_key  TEXT NOT NULL,
            payload    TEXT NOT NULL,
            updated_at TEXT,
            PRIMARY KEY (cache_name, title_key)
        )""")
    con.commit()
    con.close()
    _research_cache_ensured = True


def put_research_cache(cache_name, title_key, entry):
    """UPSERT one research-cache entry durably. Idempotent per (cache_name,
    title_key) so concurrent writers and repeated saves converge; the row is never
    deleted. `entry` is the JSON-serialisable cache value ({"scores":..,"conf":..,
    ...}). Callers wrap this best-effort so a DB hiccup never fails a prediction."""
    _ensure_research_cache()
    con = _connect()
    con.execute(
        "INSERT INTO research_cache (cache_name,title_key,payload,updated_at) "
        "VALUES (?,?,?,?) ON CONFLICT(cache_name,title_key) "
        "DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
        (cache_name, title_key, json.dumps(entry),
         dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")))
    con.commit()
    con.close()


def get_research_cache(cache_name, title_key):
    """Return one durable research-cache entry (dict) or None. Read-only; exact
    match on the caller-normalized title_key."""
    _ensure_research_cache()
    con = _connect()
    row = con.execute(
        "SELECT payload FROM research_cache WHERE cache_name=? AND title_key=?",
        (cache_name, title_key)).fetchone()
    con.close()
    return json.loads(row[0]) if row else None


# ---------------------------------------------------------------------------
# Component-correction table (DB-owned "DeltaTracker" layer)
# ---------------------------------------------------------------------------
# Per-component constant corrections + a global blend weight, versioned, with
# provenance — the app-side home for the correction layer that used to live in
# the workbook (RatingGuidelines §6C). Exactly one version is `active`. The coded
# predict path does NOT read this yet: the Opus engine's corrections are retired
# (all zero), so there is nothing to apply. It is the authoritative record and
# the surface a future recalibration writes to (a new version is inserted and the
# previous one deactivated — history is never rewritten).
def _ensure_component_corrections():
    con = _connect()
    con.execute("""
        CREATE TABLE IF NOT EXISTS component_corrections (
            id           INTEGER PRIMARY KEY,
            version      TEXT NOT NULL,
            component    TEXT NOT NULL,
            constant     REAL NOT NULL,
            blend_weight REAL NOT NULL,
            active       INTEGER NOT NULL DEFAULT 1,
            source_tag   TEXT,
            engine_hash  TEXT,
            n_books      INTEGER,
            decision     TEXT,
            note         TEXT,
            created_at   TEXT
        )""")
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# Per-user weight OVERRIDES (tenant-tailored genre weighting)
# ---------------------------------------------------------------------------
# The global genre_weights / gcomp_weights tables are the shared cold-start
# prior. These two override tables let a tenant tailor the weighting to their
# own taste WITHOUT disturbing the globals: only the rows a user changes are
# stored, and db_loader overlays them on the global defaults at load time.
# "Reset to default" is simply a row delete. Both are stored in LONG format
# (row-per-weight) so they mirror gcomp_weights and dodge Postgres-reserved
# column names ("character"). Portable DDL — runs on SQLite and Postgres alike.
def _ensure_weight_overrides():
    con = _connect()
    con.execute("""
        CREATE TABLE IF NOT EXISTS genre_weight_overrides (
            user_id  TEXT NOT NULL,
            genre    TEXT NOT NULL,
            category TEXT NOT NULL,
            weight   REAL NOT NULL,
            PRIMARY KEY (user_id, genre, category)
        )""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS gcomp_weight_overrides (
            user_id   TEXT NOT NULL,
            genre     TEXT NOT NULL,
            category  TEXT NOT NULL,
            component TEXT NOT NULL,
            weight    REAL NOT NULL,
            PRIMARY KEY (user_id, genre, category, component)
        )""")
    con.commit()
    con.close()


def set_component_corrections(version, constants, blend_weight, *, active=True,
                              source_tag=None, engine_hash=None, n_books=None,
                              decision=None, note=None):
    """Record a per-component constant-correction set (the DB-owned DeltaTracker
    layer). `constants` maps each of the canonical 14 FICTION_COMPONENTS to an
    additive constant (act−pred convention); `blend_weight` in [0,1]. When
    active=True, any previously-active version is deactivated so exactly one is
    current. A version name is write-once. Nothing commits on failure. Returns
    True on success, False otherwise."""
    con = _connect()
    try:
        missing = [c for c in FICTION_COMPONENTS if c not in constants]
        if missing:
            raise ValidationError(f"constants missing components: {missing}")
        extra = [c for c in constants if c not in FICTION_COMPONENTS]
        if extra:
            raise ValidationError(f"unknown components: {extra}")
        vals = {c: float(constants[c]) for c in FICTION_COMPONENTS}
        w = float(blend_weight)
        if not (0.0 <= w <= 1.0):
            raise ValidationError(f"blend_weight {w} out of [0,1]")
        if con.execute("SELECT 1 FROM component_corrections WHERE version=?",
                       (version,)).fetchone():
            raise ValidationError(f"correction version '{version}' already recorded")
        _backup_once()
        if active:
            con.execute("UPDATE component_corrections SET active=0 WHERE active=1")
        created = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        con.executemany(
            "INSERT INTO component_corrections (version,component,constant,"
            "blend_weight,active,source_tag,engine_hash,n_books,decision,note,"
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(version, c, vals[c], w, 1 if active else 0, source_tag, engine_hash,
              n_books, decision, note, created) for c in FICTION_COMPONENTS])
        con.commit()
        print(f"  ✓ Recorded correction set '{version}' "
              f"(14 components, blend={w}, active={active}).")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ Corrections not recorded — {e}")
        return False
    finally:
        con.close()


def get_active_corrections():
    """Return (constants {component: constant}, blend_weight, meta) for the
    currently-active correction set, or ({}, 0.0, None) if none is active."""
    con = _connect()
    try:
        rows = con.execute(
            "SELECT component, constant, blend_weight, version, source_tag, "
            "decision, n_books, created_at FROM component_corrections "
            "WHERE active=1").fetchall()
    finally:
        con.close()
    if not rows:
        return {}, 0.0, None
    constants = {r[0]: r[1] for r in rows}
    meta = {"version": rows[0][3], "source_tag": rows[0][4],
            "decision": rows[0][5], "n_books": rows[0][6], "created_at": rows[0][7]}
    return constants, float(rows[0][2]), meta


_ensure_series_number()
_ensure_delta_log()
_ensure_component_corrections()
_ensure_weight_overrides()


def _valid_genres(con, uid=None):
    """Global fiction genres, plus the user's own custom genres (override-only)
    when uid is given. Union so a tenant can add + use private genres; byte-
    identical to the old global-only behaviour when uid is None or has none."""
    genres = {r[0] for r in con.execute("SELECT genre FROM genre_weights")}
    if uid:
        genres |= {r[0] for r in con.execute(
            "SELECT DISTINCT genre FROM genre_weight_overrides WHERE user_id=?", (uid,))}
    return genres


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
             words=None, year_read=None, allow_new_genre=False, user_id=None):
    """
    Add a newly-rated fiction book. `scores` is a dict of component->value.
    Refuses to commit anything if validation fails.
    """
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        # genre check
        valid = _valid_genres(con, uid)
        if genre not in valid and not allow_new_genre:
            raise ValidationError(
                f"Genre '{genre}' is not in genre_weights. Either fix the "
                f"spelling (valid genres: {sorted(valid)}) or pass "
                f"allow_new_genre=True and add weights for it.")
        # duplicate check (scoped to this tenant — two users may share a title)
        dup = con.execute("SELECT 1 FROM books WHERE user_id=? AND title=?",
                          (uid, title)).fetchone()
        if dup:
            raise ValidationError(f"A book titled '{title}' already exists. "
                                  f"Use change_rating() to edit it.")
        # Year is any sane read year (not just the current couple) — same rule as
        # set_year_read / update_book_metadata.
        if year_read is not None and not (1900 <= int(year_read) <= 2100):
            raise ValidationError(f"Year {year_read} is out of range (1900-2100).")
        _validate_scores(scores, require_all=True)

        _backup_once()
        cols = ["title", "genre", "author", "series", "series_number", "words", "year_read", "user_id"] + FICTION_COMPONENTS
        vals = [title, genre, author, series, int(series_number) if series_number else None, words, year_read, uid] + \
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
                       allow_new_genre=False, require_scores=True, user_id=None):
    """
    Add a researched (not-yet-read) book to the recommendations table. Same
    validation discipline as add_book: genre must be known, the 14 component
    scores are range/completeness checked, and nothing commits on failure.
    Pass require_scores=False to allow adding without component scores (e.g.
    when bulk-adding series books that haven't been researched yet).
    Returns True on success, False otherwise.
    """
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        valid = _valid_genres(con, uid)
        if genre not in valid and not allow_new_genre:
            raise ValidationError(
                f"Genre '{genre}' is not in genre_weights. Either fix the "
                f"spelling (valid genres: {sorted(valid)}) or pass "
                f"allow_new_genre=True and add weights for it.")
        dup = con.execute("SELECT 1 FROM recommendations WHERE user_id=? AND title=?",
                          (uid, title)).fetchone()
        if dup:
            raise ValidationError(
                f"A recommendation titled '{title}' already exists.")
        _validate_scores(scores, require_all=require_scores)

        _backup_once()
        cols = (["title", "genre", "author", "series", "series_number", "words",
                 "done", "blurb", "keywords", "user_id"] + FICTION_COMPONENTS)
        vals = ([title, genre, author, series,
                 int(series_number) if series_number else None,
                 words, 1 if done else 0,
                 blurb, keywords, uid] + [scores.get(c) for c in FICTION_COMPONENTS])
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
def set_recommendation_meta(title, blurb=None, keywords=None, user_id=None):
    """Update only the blurb/keywords on an existing recommendation — used to
    backfill books that were added without going through research. Touches no
    component scores and no schema. Returns True on success, False otherwise."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        row = con.execute("SELECT 1 FROM recommendations WHERE user_id=? AND title=?",
                          (uid, title)).fetchone()
        if not row:
            raise ValidationError(f"No recommendation titled '{title}'.")
        _backup_once()
        con.execute("UPDATE recommendations SET blurb=?, keywords=? WHERE user_id=? AND title=?",
                    (blurb, keywords, uid, title))
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
# WRITE: replace a recommendation's component scores in place (reprediction)
# ---------------------------------------------------------------------------
def update_recommendation_scores(title, new_scores, user_id=None):
    """Replace the 14 component scores on an existing recommendation IN PLACE, by
    title — the reprediction write path. Same validation discipline as
    add_recommendation's scores (range + completeness via _validate_scores) and
    the same _backup_once guard; touches no other column (genre/author/series/
    done/blurb/words/keywords are preserved) and no schema. Returns True on
    success, False otherwise (nothing commits on failure)."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        row = con.execute("SELECT id FROM recommendations WHERE user_id=? AND title=?",
                          (uid, title)).fetchone()
        if not row:
            raise ValidationError(f"No recommendation titled '{title}' found.")
        _validate_scores(new_scores, require_all=True)
        _backup_once()
        sets = ",".join(f'"{c}"=?' for c in FICTION_COMPONENTS)
        vals = [new_scores.get(c) for c in FICTION_COMPONENTS]
        con.execute(f"UPDATE recommendations SET {sets} WHERE user_id=? AND title=?",
                    vals + [uid, title])
        con.commit()
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ '{title}' not repredicted — {e}")
        return False
    finally:
        con.close()


# ---------------------------------------------------------------------------
# WRITE: per-user weight overrides
# ---------------------------------------------------------------------------
def _coerce_weight(value, label):
    """Non-negative, finite float or a ValidationError."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"weight for '{label}' is not a number")
    if not math.isfinite(v) or v < 0:
        raise ValidationError(f"weight for '{label}' must be a finite value >= 0")
    return v


def set_genre_weights(genre, weights, user_id=None):
    """Override the FIVE category weights (Story/Character/Theme/Aesthetics/
    Worldbuilding) for one genre, for one tenant. `weights` maps each category in
    CATEGORIES to a non-negative number; values are NORMALIZED to sum 1.0 before
    storage (the WA roll-up assumes normalized category weights). Replaces any
    existing override for (user_id, genre). Returns True on success, else False
    (nothing commits on failure). Global genre_weights is never touched."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        if genre not in _valid_genres(con, uid):
            raise ValidationError(f"unknown genre '{genre}'")
        missing = [c for c in CATEGORIES if c not in weights]
        if missing:
            raise ValidationError(f"missing category weights: {missing}")
        vals = {c: _coerce_weight(weights[c], c) for c in CATEGORIES}
        total = sum(vals.values())
        if total <= 0:
            raise ValidationError("category weights sum to zero")
        _backup_once()
        con.execute("DELETE FROM genre_weight_overrides WHERE user_id=? AND genre=?",
                    (uid, genre))
        con.executemany(
            "INSERT INTO genre_weight_overrides (user_id, genre, category, weight) "
            "VALUES (?,?,?,?)",
            [(uid, genre, c, vals[c] / total) for c in CATEGORIES])
        con.commit()
        print(f"  ✓ Genre weights set for '{genre}' (user {uid[:8]}…).")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ Genre weights not set — {e}")
        return False
    finally:
        con.close()


def set_component_weights(genre, category, comp_weights, user_id=None):
    """Override the within-category COMPONENT weights for one (genre, category),
    for one tenant. `comp_weights` must map EXACTLY the components that global
    gcomp_weights defines for that (genre, category); values are NORMALIZED to sum
    1.0 (db_loader._weighted_cat_avg assumes normalized component weights).
    Replaces any existing override for (user_id, genre, category). Returns True on
    success, else False. Global gcomp_weights is never touched."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        canon = [r[0] for r in con.execute(
            "SELECT component FROM gcomp_weights WHERE genre=? AND category=?",
            (genre, category))]
        if not canon:  # a private genre: components live in the user's overrides
            canon = [r[0] for r in con.execute(
                "SELECT component FROM gcomp_weight_overrides "
                "WHERE user_id=? AND genre=? AND category=?", (uid, genre, category))]
        if not canon:
            raise ValidationError(
                f"no components defined for genre '{genre}' category '{category}'")
        if set(comp_weights) != set(canon):
            raise ValidationError(
                f"components for {genre}/{category} must be exactly {sorted(canon)}")
        vals = {c: _coerce_weight(comp_weights[c], c) for c in canon}
        total = sum(vals.values())
        if total <= 0:
            raise ValidationError("component weights sum to zero")
        _backup_once()
        con.execute(
            "DELETE FROM gcomp_weight_overrides "
            "WHERE user_id=? AND genre=? AND category=?", (uid, genre, category))
        con.executemany(
            "INSERT INTO gcomp_weight_overrides "
            "(user_id, genre, category, component, weight) VALUES (?,?,?,?,?)",
            [(uid, genre, category, c, vals[c] / total) for c in canon])
        con.commit()
        print(f"  ✓ Component weights set for '{genre}'/{category} (user {uid[:8]}…).")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ Component weights not set — {e}")
        return False
    finally:
        con.close()


def reset_weights(user_id=None, genre=None, category=None):
    """Delete a tenant's weight overrides, reverting to the global defaults.
      • genre=None                 → reset EVERYTHING for the user (both tables).
      • genre=G                    → reset genre G entirely (its category split AND
                                     all its component splits).
      • genre=G, category=C        → reset only genre G's category-C component split.
    Returns True on success, else False."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        if category is not None:
            if genre is None:
                raise ValidationError("category reset requires a genre")
            con.execute(
                "DELETE FROM gcomp_weight_overrides "
                "WHERE user_id=? AND genre=? AND category=?", (uid, genre, category))
        elif genre is not None:
            con.execute("DELETE FROM genre_weight_overrides "
                        "WHERE user_id=? AND genre=?", (uid, genre))
            con.execute("DELETE FROM gcomp_weight_overrides "
                        "WHERE user_id=? AND genre=?", (uid, genre))
        else:
            # Reset overrides on GLOBAL genres only. A user's PRIVATE genres live
            # in the same tables but are creations, not customizations of a default
            # — preserve them (remove those via delete_user_genre) so "reset all"
            # can't silently orphan books tagged with a custom genre.
            gl = _valid_genres(con)  # global only
            if gl:
                ph = ",".join("?" for _ in gl)
                con.execute(f"DELETE FROM genre_weight_overrides "
                            f"WHERE user_id=? AND genre IN ({ph})", [uid, *gl])
                con.execute(f"DELETE FROM gcomp_weight_overrides "
                            f"WHERE user_id=? AND genre IN ({ph})", [uid, *gl])
        con.commit()
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ Weights not reset — {e}")
        return False
    finally:
        con.close()


# ---------------------------------------------------------------------------
# WRITE: add / delete a PRIVATE (per-user) genre
# ---------------------------------------------------------------------------
def add_genre(genre, category_weights, user_id=None):
    """Create a PRIVATE fiction genre for one tenant: the 5 category weights
    (normalized to sum 1.0) written to genre_weight_overrides, plus EQUAL within-
    category component weights seeded into gcomp_weight_overrides so the engine can
    roll up WA for books tagged with it. The global tables are never touched, and
    the name must not collide with a global or the user's existing genre."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    cats = list(CATEGORIES)
    try:
        genre = (genre or "").strip()
        if not genre:
            raise ValidationError("genre name is required")
        if genre in _valid_genres(con, uid):
            raise ValidationError(f"genre '{genre}' already exists")
        missing = [c for c in cats if c not in category_weights]
        if missing:
            raise ValidationError(f"missing category weights: {missing}")
        vals = {c: _coerce_weight(category_weights[c], c) for c in cats}
        total = sum(vals.values())
        if total <= 0:
            raise ValidationError("category weights sum to zero")
        _backup_once()
        con.executemany(
            "INSERT INTO genre_weight_overrides (user_id, genre, category, weight) "
            "VALUES (?,?,?,?)", [(uid, genre, c, vals[c] / total) for c in cats])
        comp_rows = []
        for cat, comps in FICTION_CATEGORIES.items():
            w = 1.0 / len(comps)
            comp_rows += [(uid, genre, cat, comp, w) for comp in comps]
        con.executemany(
            "INSERT INTO gcomp_weight_overrides "
            "(user_id, genre, category, component, weight) VALUES (?,?,?,?,?)", comp_rows)
        con.commit()
        print(f"  ✓ Added private genre '{genre}' (user {uid[:8]}…).")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ Genre not added — {e}")
        return False
    finally:
        con.close()


def delete_user_genre(genre, user_id=None):
    """Delete a tenant's PRIVATE fiction genre (all its override rows). Refuses if
    it's a global genre (reset its weights instead) or if any of the user's books
    or recommendations still use it."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        genre = (genre or "").strip()
        if genre in _valid_genres(con):  # no uid → global only
            raise ValidationError(
                f"'{genre}' is a global genre — reset its weights instead of deleting.")
        if genre not in _valid_genres(con, uid):
            raise ValidationError(f"no custom genre '{genre}' to delete")
        for tbl in ("books", "recommendations"):
            n = con.execute(f"SELECT COUNT(*) FROM {tbl} WHERE user_id=? AND genre=?",
                            (uid, genre)).fetchone()[0]
            if n:
                raise ValidationError(
                    f"{n} {tbl} entr{'y' if n == 1 else 'ies'} still use '{genre}' — "
                    f"reassign them before deleting the genre.")
        _backup_once()
        con.execute("DELETE FROM genre_weight_overrides WHERE user_id=? AND genre=?",
                    (uid, genre))
        con.execute("DELETE FROM gcomp_weight_overrides WHERE user_id=? AND genre=?",
                    (uid, genre))
        con.commit()
        print(f"  ✓ Deleted private genre '{genre}' (user {uid[:8]}…).")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ Genre not deleted — {e}")
        return False
    finally:
        con.close()


# ---------------------------------------------------------------------------
# WRITE: change rating(s)
# ---------------------------------------------------------------------------
def change_rating(title, new_scores, user_id=None):
    """Update one or more component scores on an existing book."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        row = con.execute("SELECT id FROM books WHERE user_id=? AND title=?",
                          (uid, title)).fetchone()
        if not row:
            raise ValidationError(f"No book titled '{title}' found.")
        _validate_scores(new_scores, require_all=False)

        _backup_once()
        sets = ",".join(f'"{c}"=?' for c in new_scores)
        con.execute(f"UPDATE books SET {sets} WHERE user_id=? AND title=?",
                    list(new_scores.values()) + [uid, title])
        con.commit()
        changed = ", ".join(f"{c}={v}" for c, v in new_scores.items())
        print(f"  ✓ Updated '{title}': {changed}")
        _show_computed_wa(con, title)
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ Not updated — {e}")
    finally:
        con.close()


def delete_book(title, user_id=None):
    """Permanently delete a rated book by title. Backs up before writing."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        row = con.execute("SELECT id FROM books WHERE user_id=? AND title=?",
                          (uid, title)).fetchone()
        if not row:
            raise ValidationError(f"No book titled '{title}' found.")
        _backup_once()
        con.execute("DELETE FROM books WHERE user_id=? AND title=?", (uid, title))
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
def delete_recommendation(title, user_id=None):
    """Permanently delete a TBR recommendation by title. Backs up before writing."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        row = con.execute("SELECT id FROM recommendations WHERE user_id=? AND title=?",
                          (uid, title)).fetchone()
        if not row:
            raise ValidationError(f"No recommendation titled '{title}' found.")
        _backup_once()
        con.execute("DELETE FROM recommendations WHERE user_id=? AND title=?", (uid, title))
        con.commit()
        print(f"  ✓ Deleted recommendation '{title}'.")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ Not deleted — {e}")
        return False
    finally:
        con.close()


def set_status(title, status, user_id=None):
    """Set a rated book's reading status (finished / currently-reading /
    reading-next). Validated against VALID_STATUSES; nothing commits on failure.
    Returns True on success, False otherwise."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        if status not in VALID_STATUSES:
            raise ValidationError(
                f"Status '{status}' is invalid. Valid: {list(VALID_STATUSES)}.")
        row = con.execute("SELECT 1 FROM books WHERE user_id=? AND title=?",
                          (uid, title)).fetchone()
        if not row:
            raise ValidationError(f"No book titled '{title}' found.")
        _backup_once()
        con.execute("UPDATE books SET status=? WHERE user_id=? AND title=?", (status, uid, title))
        con.commit()
        print(f"  ✓ '{title}' status set to {status}.")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ {e}")
        return False
    finally:
        con.close()


def set_series_number(table: str, title: str, number, user_id=None):
    """Set series_number on a row in 'books' or 'recommendations'.
    Accepts int or float (e.g. 0.5 for prologues, 3.5 for interstitials).
    Backs up once per session before writing. Returns True on success."""
    if table not in ("books", "recommendations"):
        raise ValidationError(f"Unknown table '{table}'. Use 'books' or 'recommendations'.")
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        row = con.execute(f"SELECT 1 FROM {table} WHERE user_id=? AND title=?",
                          (uid, title)).fetchone()
        if not row:
            raise ValidationError(f"No entry titled '{title}' in {table}.")
        _backup_once()
        val = float(number) if number != int(number) else int(number)
        con.execute(f"UPDATE {table} SET series_number=? WHERE user_id=? AND title=?", (val, uid, title))
        con.commit()
        print(f"  ✓ {table}.series_number = {val} for '{title}'.")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ {e}")
        return False
    finally:
        con.close()


def set_year_read(title, year, user_id=None):
    """Set/edit the year a rated book was read. Range-checked; nothing commits on
    failure. Returns True on success, False otherwise."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        if year is not None and not (1900 <= int(year) <= 2100):
            raise ValidationError(f"Year {year} is out of range (1900-2100).")
        row = con.execute("SELECT 1 FROM books WHERE user_id=? AND title=?",
                          (uid, title)).fetchone()
        if not row:
            raise ValidationError(f"No book titled '{title}' found.")
        _backup_once()
        con.execute("UPDATE books SET year_read=? WHERE user_id=? AND title=?",
                    (int(year) if year is not None else None, uid, title))
        con.commit()
        print(f"  ✓ '{title}' year_read set to {year}.")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ {e}")
        return False
    finally:
        con.close()


def set_read_month(title, month, user_id=None):
    """Set/edit the month (1-12, or None to clear) a rated book was read. This
    complements set_year_read; the year stays the authoritative read-year. Nothing
    commits on failure. Returns True on success, False otherwise."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        if month is not None and not (1 <= int(month) <= 12):
            raise ValidationError(f"Month {month} is out of range (1-12).")
        row = con.execute("SELECT 1 FROM books WHERE user_id=? AND title=?",
                          (uid, title)).fetchone()
        if not row:
            raise ValidationError(f"No book titled '{title}' found.")
        _backup_once()
        con.execute("UPDATE books SET read_month=? WHERE user_id=? AND title=?",
                    (int(month) if month is not None else None, uid, title))
        con.commit()
        print(f"  ✓ '{title}' read_month set to {month}.")
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
def set_done(title, done=True, user_id=None):
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        row = con.execute("SELECT 1 FROM recommendations WHERE user_id=? AND title=?",
                          (uid, title)).fetchone()
        if not row:
            raise ValidationError(f"No recommendation titled '{title}'.")
        _backup_once()
        con.execute("UPDATE recommendations SET done=? WHERE user_id=? AND title=?",
                    (1 if done else 0, uid, title))
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
def update_queue(titles, user_id=None):
    """Replace the read queue with this ordered list of titles (per tenant)."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        _backup_once()
        con.execute("DELETE FROM read_queue WHERE user_id=?", (uid,))
        for pos, t in enumerate(titles, 1):
            con.execute("INSERT INTO read_queue (position,title,user_id) VALUES (?,?,?)",
                        (pos, t, uid))
        con.commit()
        print(f"  ✓ Queue updated ({len(titles)} books).")
    finally:
        con.close()


# ---------------------------------------------------------------------------
# WRITE: record a prediction-vs-actual delta when a forecast book gets rated
# ---------------------------------------------------------------------------
def log_delta(title: str, pred_scores: dict, pred_wa: float,
              act_scores: dict, act_wa: float,
              pred_model: str = None, meta: dict = None,
              user_id: str = None) -> None:
    """
    Record predicted vs actual component scores and WA delta for a book that
    previously had a stored prediction. pred_scores / act_scores are both
    dicts keyed by the canonical 14 component names. Records deltas as
    (actual − predicted) — matching backfill_delta_log and the DeltaTracker
    sheet (positive == underprediction). Never raises — delta logging is
    non-fatal.

    pred_model tags which research model produced the prediction; it defaults
    to RESEARCH_MODEL (the current Opus pipeline) so live pairs accrue under
    the Opus tag for later isolated recalibration. Historical Sonnet-era rows
    stay NULL and are never relabeled.

    meta (optional) carries the prediction-mechanism metadata for this row:
    any subset of DELTA_META_COLUMNS keys (genre/author/words, analog counts,
    correction split, CI, confidence). Only recognised, non-None keys are
    written; anything absent stays NULL. meta=None reproduces the pre-upgrade
    write exactly, so backfill_delta_log and any legacy caller are unaffected.
    """
    try:
        logged_at = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        uid = user_id or db_backend.DEFAULT_USER_ID
        pred_cols = [f'"pred_{_col(c)}"' for c in FICTION_COMPONENTS]
        act_cols  = [f'"act_{_col(c)}"'  for c in FICTION_COMPONENTS]
        d_cols    = [f'"d_{_col(c)}"'    for c in FICTION_COMPONENTS]
        all_cols  = (["title", "logged_at", "pred_wa", "act_wa", "d_wa"]
                     + pred_cols + act_cols + d_cols + ["pred_model", "user_id"])
        pred_vals = [pred_scores.get(c) for c in FICTION_COMPONENTS]
        act_vals  = [act_scores.get(c)  for c in FICTION_COMPONENTS]
        d_vals    = [
            (act_scores.get(c) - pred_scores.get(c))
            if pred_scores.get(c) is not None and act_scores.get(c) is not None
            else None
            for c in FICTION_COMPONENTS
        ]
        model_tag = pred_model if pred_model is not None else RESEARCH_MODEL
        all_vals = (
            [title, logged_at, pred_wa, act_wa, (act_wa - pred_wa)]
            + pred_vals + act_vals + d_vals + [model_tag, uid]
        )
        # Append recognised mechanism-metadata fields (whitelisted against
        # DELTA_META_COLUMNS so a stray key can never inject a column name).
        if meta:
            for name, _typ in DELTA_META_COLUMNS:
                if meta.get(name) is not None:
                    all_cols.append(name)
                    all_vals.append(meta[name])
        ph = ",".join("?" for _ in all_vals)
        col_str = ",".join(all_cols)
        con = _connect()
        con.execute(f"INSERT INTO delta_log ({col_str}) VALUES ({ph})", all_vals)
        con.commit()
        con.close()
        print(f"  (delta logged for '{title}': d_wa={act_wa - pred_wa:+.3f}, "
              f"model={model_tag})")
    except Exception as exc:
        print(f"  (delta log skipped for '{title}': {exc})")


# ---------------------------------------------------------------------------
# WRITE: bulk backfill of HISTORICAL prediction-vs-actual rows into delta_log
# ---------------------------------------------------------------------------
# Sentinel stamped on every backfilled row's logged_at, so a batch import is
# distinguishable from live-logged rows (which carry a real wall-clock UTC time
# such as 2026-06-30T04:24:55Z). It doubles as the idempotency key: re-running
# the backfill first clears rows bearing this exact marker and never touches
# any row with a different logged_at (i.e. the live-logged rows are safe).
DELTA_BACKFILL_MARKER = "2026-06-27T00:00:00Z"   # BookRankingsNew.xlsx snapshot date

# SIGN CONVENTION (important): backfill_delta_log stores d_* = actual - predicted,
# matching the workbook's DeltaTracker sheet (a positive delta == underprediction).
# The live path log_delta() above uses the SAME convention, so every delta_log row
# — live or backfilled — is act - pred. resync_delta_log_signs() below enforces
# this invariant across the table (and repaired the one legacy pred - act row).


def backfill_delta_log(records, logged_at=DELTA_BACKFILL_MARKER, replace=True):
    """
    Bulk-insert historical predicted-vs-actual rows into delta_log.

    Each record is a dict:
        {
          "title":   str,            # stored verbatim (use the books-table title)
          "pred_wa": float,          # TBRFinished 'Predicted Score'
          "act_wa":  float,          # engine WA from the books table
          "pred":    {comp: value},  # all 14 canonical FICTION_COMPONENTS
          "act":     {comp: value},  # all 14 canonical FICTION_COMPONENTS
        }

    Deltas are computed HERE as (actual - predicted) so a caller cannot get the
    sign wrong (see DELTA_BACKFILL_MARKER note about the opposite live convention).

    `logged_at` is stamped on every row as a clearly-historical backfill marker.
    With replace=True the write is idempotent: rows already bearing this exact
    logged_at marker are deleted first, then the batch is inserted in one
    transaction. Rows with any other logged_at are never touched.

    Validation mirrors the rest of db_write: each record must have a non-empty
    title, a numeric pred_wa/act_wa, and every one of the 14 components present
    and in range 0-10 on BOTH sides (worldbuilding included — history rows are
    complete). Malformed records are SKIPPED (never fabricated) and returned in
    the report; a partial/invalid record writes nothing.

    Returns: {"inserted": int, "deleted": int, "skipped": [(title, reason), ...]}
    """
    _backup_once()
    pred_cols = [f'"pred_{_col(c)}"' for c in FICTION_COMPONENTS]
    act_cols  = [f'"act_{_col(c)}"'  for c in FICTION_COMPONENTS]
    d_cols    = [f'"d_{_col(c)}"'    for c in FICTION_COMPONENTS]
    all_cols  = (["title", "logged_at", "pred_wa", "act_wa", "d_wa"]
                 + pred_cols + act_cols + d_cols)
    col_str = ",".join(all_cols)
    ph = ",".join("?" for _ in all_cols)

    valid_rows, skipped = [], []
    for rec in records:
        title = (rec.get("title") or "").strip()
        pred  = rec.get("pred") or {}
        act   = rec.get("act") or {}
        try:
            if not title:
                raise ValidationError("empty title")
            if rec.get("pred_wa") is None or rec.get("act_wa") is None:
                raise ValidationError("missing pred_wa/act_wa")
            # Completeness/range: the 11 core components are required on both
            # sides; worldbuilding (Depth2/Integration/Originality) is optional
            # and legitimately blank for realist genres — same rule as
            # _validate_scores. A missing worldbuilding value stays None (never
            # fabricated), and its delta is left None (undefined without both).
            for side_name, side in (("pred", pred), ("act", act)):
                for c in FICTION_COMPONENTS:
                    v = side.get(c)
                    if v is None:
                        if c in WORLDBUILDING:
                            continue
                        raise ValidationError(f"missing {side_name} component '{c}'")
                    if not (0 <= float(v) <= 10):
                        raise ValidationError(f"{side_name} '{c}'={v} out of range 0-10")
            pw = float(rec["pred_wa"])
            aw = float(rec["act_wa"])
            def _f(x):
                return None if x is None else float(x)
            pred_vals = [_f(pred.get(c)) for c in FICTION_COMPONENTS]
            act_vals  = [_f(act.get(c))  for c in FICTION_COMPONENTS]
            d_vals    = [(a - p) if (p is not None and a is not None) else None
                         for p, a in zip(pred_vals, act_vals)]  # actual - predicted
            valid_rows.append([title, logged_at, pw, aw, (aw - pw)]
                              + pred_vals + act_vals + d_vals)
        except (ValidationError, TypeError, ValueError) as exc:
            skipped.append((title or "<no title>", str(exc)))

    con = _connect()
    try:
        deleted = 0
        if replace:
            deleted = con.execute(
                "DELETE FROM delta_log WHERE logged_at=?", (logged_at,)).rowcount
        if valid_rows:
            con.executemany(
                f"INSERT INTO delta_log ({col_str}) VALUES ({ph})", valid_rows)
        con.commit()
    finally:
        con.close()
    print(f"  (delta_log backfill: inserted {len(valid_rows)}, "
          f"deleted {deleted} prior marker rows, skipped {len(skipped)})")
    return {"inserted": len(valid_rows), "deleted": deleted, "skipped": skipped}


def _num_eq(a, b, tol=1e-9):
    """None-aware numeric equality with a float tolerance."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


def resync_delta_log_signs():
    """
    Enforce the canonical delta convention d = actual - predicted on every
    delta_log row by recomputing d_wa and all d_* columns from that row's OWN
    stored pred_wa/act_wa and pred_*/act_* values. pred_*/act_* are never
    touched — only the derived deltas are rewritten, so nothing is fabricated.

    Idempotent: rows already stored as act - pred (e.g. everything written by
    backfill_delta_log) are recomputed to the identical values and left alone.
    Its purpose is to repair legacy rows written by the old log_delta(), which
    stored predicted - actual. A NULL pred or act yields a NULL delta.

    Returns the number of rows whose stored deltas actually changed.
    """
    _backup_once()
    cols = (["id", "pred_wa", "act_wa", "d_wa"]
            + [f"pred_{_col(c)}" for c in FICTION_COMPONENTS]
            + [f"act_{_col(c)}"  for c in FICTION_COMPONENTS]
            + [f"d_{_col(c)}"    for c in FICTION_COMPONENTS])
    quoted = ",".join(f'"{c}"' for c in cols)
    con = _connect()
    try:
        changed = 0
        for row in con.execute(f"SELECT {quoted} FROM delta_log").fetchall():
            r = dict(zip(cols, row))
            updates = {}
            new_dwa = (r["act_wa"] - r["pred_wa"]) if _both(r["act_wa"], r["pred_wa"]) else None
            if not _num_eq(new_dwa, r["d_wa"]):
                updates["d_wa"] = new_dwa
            for c in FICTION_COMPONENTS:
                pk, ak, dk = f"pred_{_col(c)}", f"act_{_col(c)}", f"d_{_col(c)}"
                new_d = (r[ak] - r[pk]) if _both(r[ak], r[pk]) else None
                if not _num_eq(new_d, r[dk]):
                    updates[dk] = new_d
            if updates:
                set_clause = ",".join(f'"{k}"=?' for k in updates)
                con.execute(f'UPDATE delta_log SET {set_clause} WHERE id=?',
                            list(updates.values()) + [r["id"]])
                changed += 1
        con.commit()
    finally:
        con.close()
    print(f"  (delta_log sign resync: {changed} row(s) rewritten to d = act - pred)")
    return changed


def _both(a, b):
    return a is not None and b is not None


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
    # TBR (to-be-read) pair, mirroring the fiction recommendations + read_queue.
    # nonfiction_recommendations stores RAW components only (no derived columns);
    # Total Average / WA / rank are computed on read by the nonfiction engine.
    con.execute('''
        CREATE TABLE IF NOT EXISTS nonfiction_recommendations (
            id          INTEGER PRIMARY KEY,
            title       TEXT NOT NULL,
            genre       TEXT,
            author      TEXT,
            series      TEXT,
            words       INTEGER,
            done        INTEGER DEFAULT 0,
            blurb       TEXT,
            keywords    TEXT,
            "Informativeness"        REAL,
            "Argumentation"          REAL,
            "Entertainment"          REAL,
            "Prose"                  REAL,
            "Phraseology"            REAL,
            "Insights"               REAL,
            "Philosophizing"         REAL,
            "Thought-Provokingness"  REAL,
            series_number  INTEGER
        )
    ''')
    con.execute('''
        CREATE TABLE IF NOT EXISTS nonfiction_read_queue (
            position INTEGER PRIMARY KEY,
            title    TEXT NOT NULL
        )
    ''')
    # Per-user weight overrides (nonfiction mirror of genre_weight_overrides /
    # gcomp_weight_overrides). Long format; category stored capitalized
    # (Quality/Aesthetics/Theme) to match nonfiction_gcomp_weights and the API.
    con.execute('''
        CREATE TABLE IF NOT EXISTS nonfiction_genre_weight_overrides (
            user_id  TEXT NOT NULL,
            genre    TEXT NOT NULL,
            category TEXT NOT NULL,
            weight   REAL NOT NULL,
            PRIMARY KEY (user_id, genre, category)
        )
    ''')
    con.execute('''
        CREATE TABLE IF NOT EXISTS nonfiction_gcomp_weight_overrides (
            user_id   TEXT NOT NULL,
            genre     TEXT NOT NULL,
            category  TEXT NOT NULL,
            component TEXT NOT NULL,
            weight    REAL NOT NULL,
            PRIMARY KEY (user_id, genre, category, component)
        )
    ''')
    con.commit()
    con.close()


def _valid_nonfiction_genres(con, uid=None):
    """Global nonfiction genres, plus the user's own custom genres when uid is
    given (see _valid_genres). Byte-identical when uid is None or has none."""
    genres = {r[0] for r in con.execute("SELECT genre FROM nonfiction_genre_weights")}
    if uid:
        genres |= {r[0] for r in con.execute(
            "SELECT DISTINCT genre FROM nonfiction_genre_weight_overrides WHERE user_id=?",
            (uid,))}
    return genres


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
                        allow_new_genre=False, require_scores=True, user_id=None):
    """Add a nonfiction book to nonfiction_books, mirroring add_book's
    discipline: duplicate title refused, scores range/completeness checked,
    nothing commits on failure. The three category averages + Total Average are
    recomputed here and stored; WA is left NULL (no nonfiction weights yet).
    genre is optional and only validated once nonfiction_genre_weights is
    populated. Returns True on success, False otherwise."""
    scores = scores or {}
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        if status not in VALID_STATUSES:
            raise ValidationError(
                f"Status '{status}' is invalid. Valid: {list(VALID_STATUSES)}.")
        dup = con.execute("SELECT 1 FROM nonfiction_books WHERE user_id=? AND title=?",
                          (uid, title)).fetchone()
        if dup:
            raise ValidationError(
                f"A nonfiction book titled '{title}' already exists. "
                f"Use change_nonfiction_rating() to edit it.")
        if year_read is not None and not (1900 <= int(year_read) <= 2100):
            raise ValidationError(f"Year {year_read} is out of range (1900-2100).")
        valid = _valid_nonfiction_genres(con, uid)  # empty until weights are added
        if genre is not None and valid and genre not in valid and not allow_new_genre:
            raise ValidationError(
                f"Genre '{genre}' is not in nonfiction_genre_weights "
                f"(valid: {sorted(valid)}). Pass allow_new_genre=True to override.")
        _validate_nonfiction_scores(scores, require_all=require_scores)

        _backup_once()
        avgs = _nonfiction_averages(scores)
        cols = (["title", "genre", "author", "series", "series_number",
                 "words", "year_read", "status", "user_id"]
                + NONFICTION_COMPONENTS
                + ["Quality Average", "Aesthetics Average",
                   "Theme Average", "Total Average"])
        vals = ([title, genre, author, series,
                 int(series_number) if series_number else None,
                 words, year_read, status, uid]
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


def change_nonfiction_rating(title, new_scores, user_id=None):
    """Update one or more component scores on a nonfiction book, then recompute
    and store the category averages + Total Average so they stay consistent.
    WA is not touched (stays NULL until the nonfiction engine owns it)."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        row = con.execute("SELECT 1 FROM nonfiction_books WHERE user_id=? AND title=?",
                          (uid, title)).fetchone()
        if not row:
            raise ValidationError(f"No nonfiction book titled '{title}' found.")
        _validate_nonfiction_scores(new_scores, require_all=False)

        _backup_once()
        sets = ",".join(f'"{c}"=?' for c in new_scores)
        con.execute(f"UPDATE nonfiction_books SET {sets} WHERE user_id=? AND title=?",
                    list(new_scores.values()) + [uid, title])
        # recompute averages from the full, now-updated component row
        cur = con.execute(
            f'SELECT {",".join(chr(34)+c+chr(34) for c in NONFICTION_COMPONENTS)} '
            f'FROM nonfiction_books WHERE user_id=? AND title=?', (uid, title))
        full = dict(zip(NONFICTION_COMPONENTS, cur.fetchone()))
        avgs = _nonfiction_averages(full)
        con.execute('UPDATE nonfiction_books SET "Quality Average"=?, '
                    '"Aesthetics Average"=?, "Theme Average"=?, '
                    '"Total Average"=? WHERE user_id=? AND title=?',
                    [avgs["Quality Average"], avgs["Aesthetics Average"],
                     avgs["Theme Average"], avgs["Total Average"], uid, title])
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


def delete_nonfiction_book(title, user_id=None):
    """Permanently delete a nonfiction book by title. Backs up before writing."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        row = con.execute("SELECT 1 FROM nonfiction_books WHERE user_id=? AND title=?",
                          (uid, title)).fetchone()
        if not row:
            raise ValidationError(f"No nonfiction book titled '{title}' found.")
        _backup_once()
        con.execute("DELETE FROM nonfiction_books WHERE user_id=? AND title=?", (uid, title))
        con.commit()
        print(f"  ✓ Deleted nonfiction '{title}'.")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ Not deleted — {e}")
        return False
    finally:
        con.close()


def set_nonfiction_status(title, status, user_id=None):
    """Set a nonfiction book's reading status (finished / currently-reading /
    reading-next). Validated against VALID_STATUSES. Returns True on success."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        if status not in VALID_STATUSES:
            raise ValidationError(
                f"Status '{status}' is invalid. Valid: {list(VALID_STATUSES)}.")
        row = con.execute("SELECT 1 FROM nonfiction_books WHERE user_id=? AND title=?",
                          (uid, title)).fetchone()
        if not row:
            raise ValidationError(f"No nonfiction book titled '{title}' found.")
        _backup_once()
        con.execute("UPDATE nonfiction_books SET status=? WHERE user_id=? AND title=?",
                    (status, uid, title))
        con.commit()
        print(f"  ✓ Nonfiction '{title}' status set to {status}.")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ {e}")
        return False
    finally:
        con.close()


def set_nonfiction_year_read(title, year, user_id=None):
    """Set/edit the year a nonfiction book was read. Range-checked 1900-2100.
    Returns True on success, False otherwise."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        if year is not None and not (1900 <= int(year) <= 2100):
            raise ValidationError(f"Year {year} is out of range (1900-2100).")
        row = con.execute("SELECT 1 FROM nonfiction_books WHERE user_id=? AND title=?",
                          (uid, title)).fetchone()
        if not row:
            raise ValidationError(f"No nonfiction book titled '{title}' found.")
        _backup_once()
        con.execute("UPDATE nonfiction_books SET year_read=? WHERE user_id=? AND title=?",
                    (int(year) if year is not None else None, uid, title))
        con.commit()
        print(f"  ✓ Nonfiction '{title}' year_read set to {year}.")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ {e}")
        return False
    finally:
        con.close()


def set_nonfiction_read_month(title, month, user_id=None):
    """Set/edit the month (1-12, or None to clear) a nonfiction book was read.
    Mirrors set_read_month for the nonfiction_books table. Returns True/False."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        if month is not None and not (1 <= int(month) <= 12):
            raise ValidationError(f"Month {month} is out of range (1-12).")
        row = con.execute("SELECT 1 FROM nonfiction_books WHERE user_id=? AND title=?",
                          (uid, title)).fetchone()
        if not row:
            raise ValidationError(f"No nonfiction book titled '{title}' found.")
        _backup_once()
        con.execute("UPDATE nonfiction_books SET read_month=? WHERE user_id=? AND title=?",
                    (int(month) if month is not None else None, uid, title))
        con.commit()
        print(f"  ✓ Nonfiction '{title}' read_month set to {month}.")
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


def seed_nonfiction_weights(quality=0.45, aesthetics=0.20, theme=0.35,
                            genre="Nonfiction"):
    """Seed (or replace) the nonfiction weight tables with one default genre
    profile, used for every nonfiction book until a finer nonfiction genre
    taxonomy exists. The category weights (quality/aesthetics/theme) set how the
    nonfiction WA leans across the three categories and must sum to 1.0. Within
    each category the components are EQUAL-weighted, reproducing the workbook's
    plain-AVERAGE category means (so WA differs from Total Average only by the
    category lean). Retune anytime by calling again with new category weights —
    the nonfiction engine reads these tables live, so WA updates on next load.
    Returns True on success, False otherwise.

    Default Quality 0.45 / Aesthetics 0.20 / Theme 0.35 was chosen by the owner
    (the workbook defines no nonfiction weighting — it ranks nonfiction by the
    unweighted Total Average)."""
    cat_w = {"Quality": float(quality), "Aesthetics": float(aesthetics),
             "Theme": float(theme)}
    con = _connect()
    try:
        for k, v in cat_w.items():
            if not (0 <= v <= 1):
                raise ValidationError(f"{k} weight {v} is out of range (0-1).")
        if abs(sum(cat_w.values()) - 1.0) > 1e-6:
            raise ValidationError(
                f"Category weights must sum to 1.0 (got {sum(cat_w.values()):.4f}).")
        _backup_once()
        con.execute("DELETE FROM nonfiction_genre_weights WHERE genre=?", (genre,))
        con.execute("DELETE FROM nonfiction_gcomp_weights WHERE genre=?", (genre,))
        con.execute("INSERT INTO nonfiction_genre_weights "
                    "(genre,quality,aesthetics,theme) VALUES (?,?,?,?)",
                    (genre, cat_w["Quality"], cat_w["Aesthetics"], cat_w["Theme"]))
        for cat, comps in NONFICTION_CATEGORIES.items():
            w = 1.0 / len(comps)
            for comp in comps:
                con.execute("INSERT INTO nonfiction_gcomp_weights "
                            "(genre,category,component,weight) VALUES (?,?,?,?)",
                            (genre, cat, comp, w))
        con.commit()
        print(f"  ✓ Seeded nonfiction weights for '{genre}': "
              f"Quality {cat_w['Quality']:.2f} / Aesthetics {cat_w['Aesthetics']:.2f}"
              f" / Theme {cat_w['Theme']:.2f} (components equal-weighted in-category).")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ Not seeded — {e}")
        return False
    finally:
        con.close()


# ---------------------------------------------------------------------------
# WRITE: per-user nonfiction weight overrides (mirror of the fiction pair)
# ---------------------------------------------------------------------------
def set_nonfiction_genre_weights(genre, weights, user_id=None):
    """Override the nonfiction category weights (Quality/Aesthetics/Theme) for one
    genre, for one tenant. Values normalized to sum 1.0. The nonfiction mirror of
    set_genre_weights; global nonfiction_genre_weights is never touched."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    cats = list(NONFICTION_CATEGORIES)
    try:
        if genre not in _valid_nonfiction_genres(con, uid):
            raise ValidationError(f"unknown nonfiction genre '{genre}'")
        missing = [c for c in cats if c not in weights]
        if missing:
            raise ValidationError(f"missing category weights: {missing}")
        vals = {c: _coerce_weight(weights[c], c) for c in cats}
        total = sum(vals.values())
        if total <= 0:
            raise ValidationError("category weights sum to zero")
        _backup_once()
        con.execute("DELETE FROM nonfiction_genre_weight_overrides "
                    "WHERE user_id=? AND genre=?", (uid, genre))
        con.executemany(
            "INSERT INTO nonfiction_genre_weight_overrides "
            "(user_id, genre, category, weight) VALUES (?,?,?,?)",
            [(uid, genre, c, vals[c] / total) for c in cats])
        con.commit()
        print(f"  ✓ Nonfiction genre weights set for '{genre}' (user {uid[:8]}…).")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ Nonfiction genre weights not set — {e}")
        return False
    finally:
        con.close()


def set_nonfiction_component_weights(genre, category, comp_weights, user_id=None):
    """Override the within-category component weights for one nonfiction
    (genre, category). `comp_weights` must map exactly that group's components
    (per nonfiction_gcomp_weights); normalized to sum 1.0. Nonfiction mirror of
    set_component_weights."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        canon = [r[0] for r in con.execute(
            "SELECT component FROM nonfiction_gcomp_weights "
            "WHERE genre=? AND category=?", (genre, category))]
        if not canon:  # a private genre: components live in the user's overrides
            canon = [r[0] for r in con.execute(
                "SELECT component FROM nonfiction_gcomp_weight_overrides "
                "WHERE user_id=? AND genre=? AND category=?", (uid, genre, category))]
        if not canon:
            raise ValidationError(
                f"no components for nonfiction genre '{genre}' category '{category}'")
        if set(comp_weights) != set(canon):
            raise ValidationError(
                f"components for {genre}/{category} must be exactly {sorted(canon)}")
        vals = {c: _coerce_weight(comp_weights[c], c) for c in canon}
        total = sum(vals.values())
        if total <= 0:
            raise ValidationError("component weights sum to zero")
        _backup_once()
        con.execute("DELETE FROM nonfiction_gcomp_weight_overrides "
                    "WHERE user_id=? AND genre=? AND category=?", (uid, genre, category))
        con.executemany(
            "INSERT INTO nonfiction_gcomp_weight_overrides "
            "(user_id, genre, category, component, weight) VALUES (?,?,?,?,?)",
            [(uid, genre, category, c, vals[c] / total) for c in canon])
        con.commit()
        print(f"  ✓ Nonfiction component weights set for '{genre}'/{category} (user {uid[:8]}…).")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ Nonfiction component weights not set — {e}")
        return False
    finally:
        con.close()


def reset_nonfiction_weights(user_id=None, genre=None, category=None):
    """Delete a tenant's nonfiction weight overrides (revert to defaults). Same
    scoping as reset_weights: all (no args) / one genre / one component split."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        if category is not None:
            if genre is None:
                raise ValidationError("category reset requires a genre")
            con.execute("DELETE FROM nonfiction_gcomp_weight_overrides "
                        "WHERE user_id=? AND genre=? AND category=?", (uid, genre, category))
        elif genre is not None:
            con.execute("DELETE FROM nonfiction_genre_weight_overrides "
                        "WHERE user_id=? AND genre=?", (uid, genre))
            con.execute("DELETE FROM nonfiction_gcomp_weight_overrides "
                        "WHERE user_id=? AND genre=?", (uid, genre))
        else:
            # Global genres only — preserve the user's private genres (see reset_weights).
            gl = _valid_nonfiction_genres(con)  # global only
            if gl:
                ph = ",".join("?" for _ in gl)
                con.execute(f"DELETE FROM nonfiction_genre_weight_overrides "
                            f"WHERE user_id=? AND genre IN ({ph})", [uid, *gl])
                con.execute(f"DELETE FROM nonfiction_gcomp_weight_overrides "
                            f"WHERE user_id=? AND genre IN ({ph})", [uid, *gl])
        con.commit()
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ Nonfiction weights not reset — {e}")
        return False
    finally:
        con.close()


def add_nonfiction_genre(genre, category_weights, user_id=None):
    """Create a PRIVATE nonfiction genre for one tenant (mirror of add_genre):
    the Quality/Aesthetics/Theme category weights + equal component seeds, written
    to the nonfiction override tables. Global tables untouched; no name collision."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    cats = list(NONFICTION_CATEGORIES)  # dict keys → Quality/Aesthetics/Theme
    try:
        genre = (genre or "").strip()
        if not genre:
            raise ValidationError("genre name is required")
        if genre in _valid_nonfiction_genres(con, uid):
            raise ValidationError(f"nonfiction genre '{genre}' already exists")
        missing = [c for c in cats if c not in category_weights]
        if missing:
            raise ValidationError(f"missing category weights: {missing}")
        vals = {c: _coerce_weight(category_weights[c], c) for c in cats}
        total = sum(vals.values())
        if total <= 0:
            raise ValidationError("category weights sum to zero")
        _backup_once()
        con.executemany(
            "INSERT INTO nonfiction_genre_weight_overrides (user_id, genre, category, weight) "
            "VALUES (?,?,?,?)", [(uid, genre, c, vals[c] / total) for c in cats])
        comp_rows = []
        for cat, comps in NONFICTION_CATEGORIES.items():
            w = 1.0 / len(comps)
            comp_rows += [(uid, genre, cat, comp, w) for comp in comps]
        con.executemany(
            "INSERT INTO nonfiction_gcomp_weight_overrides "
            "(user_id, genre, category, component, weight) VALUES (?,?,?,?,?)", comp_rows)
        con.commit()
        print(f"  ✓ Added private nonfiction genre '{genre}' (user {uid[:8]}…).")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ Nonfiction genre not added — {e}")
        return False
    finally:
        con.close()


def delete_nonfiction_user_genre(genre, user_id=None):
    """Delete a tenant's PRIVATE nonfiction genre. Refuses on a global genre or if
    any of the user's nonfiction books / recommendations still use it."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        genre = (genre or "").strip()
        if genre in _valid_nonfiction_genres(con):  # global only
            raise ValidationError(
                f"'{genre}' is a global genre — reset its weights instead of deleting.")
        if genre not in _valid_nonfiction_genres(con, uid):
            raise ValidationError(f"no custom nonfiction genre '{genre}' to delete")
        for tbl in ("nonfiction_books", "nonfiction_recommendations"):
            n = con.execute(f"SELECT COUNT(*) FROM {tbl} WHERE user_id=? AND genre=?",
                            (uid, genre)).fetchone()[0]
            if n:
                raise ValidationError(
                    f"{n} {tbl} entr{'y' if n == 1 else 'ies'} still use '{genre}' — "
                    f"reassign them before deleting the genre.")
        _backup_once()
        con.execute("DELETE FROM nonfiction_genre_weight_overrides WHERE user_id=? AND genre=?",
                    (uid, genre))
        con.execute("DELETE FROM nonfiction_gcomp_weight_overrides WHERE user_id=? AND genre=?",
                    (uid, genre))
        con.commit()
        print(f"  ✓ Deleted private nonfiction genre '{genre}' (user {uid[:8]}…).")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ Nonfiction genre not deleted — {e}")
        return False
    finally:
        con.close()


# ---------------------------------------------------------------------------
# NONFICTION TBR — recommendations + read queue (mirror the fiction twins).
# Writes ONLY to the nonfiction tables; the fiction TBR is never touched.
# ---------------------------------------------------------------------------
def add_nonfiction_recommendation(title, author=None, genre=None, scores=None,
                                  series=None, series_number=None, words=None,
                                  blurb=None, keywords=None, done=0,
                                  allow_new_genre=False, require_scores=True,
                                  user_id=None):
    """Add a researched (not-yet-read) nonfiction book to
    nonfiction_recommendations. Stores RAW components only — Total Average / WA
    are computed on read by the nonfiction engine. Refuses duplicate titles;
    nothing commits on failure. Returns True on success, False otherwise."""
    scores = scores or {}
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        dup = con.execute("SELECT 1 FROM nonfiction_recommendations WHERE user_id=? AND title=?",
                          (uid, title)).fetchone()
        if dup:
            raise ValidationError(
                f"A nonfiction recommendation titled '{title}' already exists.")
        valid = _valid_nonfiction_genres(con, uid)  # empty until weights add genres
        if genre is not None and valid and genre not in valid and not allow_new_genre:
            raise ValidationError(
                f"Genre '{genre}' is not in nonfiction_genre_weights "
                f"(valid: {sorted(valid)}). Pass allow_new_genre=True to override.")
        _validate_nonfiction_scores(scores, require_all=require_scores)

        _backup_once()
        cols = (["title", "genre", "author", "series", "series_number", "words",
                 "done", "blurb", "keywords", "user_id"] + NONFICTION_COMPONENTS)
        vals = ([title, genre, author, series,
                 int(series_number) if series_number else None, words,
                 1 if done else 0, blurb, keywords, uid]
                + [scores.get(c) for c in NONFICTION_COMPONENTS])
        ph = ",".join("?" for _ in cols)
        con.execute(f'INSERT INTO nonfiction_recommendations '
                    f'({",".join(chr(34)+c+chr(34) for c in cols)}) VALUES ({ph})',
                    vals)
        con.commit()
        print(f"  ✓ Saved '{title}' to nonfiction recommendations ({author or '—'}).")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ NOT saved — {e}")
        return False
    finally:
        con.close()


def set_nonfiction_recommendation_meta(title, blurb=None, keywords=None, user_id=None):
    """Update only the blurb/keywords on a nonfiction recommendation."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        row = con.execute("SELECT 1 FROM nonfiction_recommendations WHERE user_id=? AND title=?",
                          (uid, title)).fetchone()
        if not row:
            raise ValidationError(f"No nonfiction recommendation titled '{title}'.")
        _backup_once()
        con.execute("UPDATE nonfiction_recommendations SET blurb=?, keywords=? "
                    "WHERE user_id=? AND title=?", (blurb, keywords, uid, title))
        con.commit()
        print(f"  ✓ Updated blurb/keywords for '{title}'.")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ {e}")
        return False
    finally:
        con.close()


def delete_nonfiction_recommendation(title, user_id=None):
    """Permanently delete a nonfiction TBR recommendation by title."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        row = con.execute("SELECT 1 FROM nonfiction_recommendations WHERE user_id=? AND title=?",
                          (uid, title)).fetchone()
        if not row:
            raise ValidationError(f"No nonfiction recommendation titled '{title}'.")
        _backup_once()
        con.execute("DELETE FROM nonfiction_recommendations WHERE user_id=? AND title=?", (uid, title))
        con.commit()
        print(f"  ✓ Deleted nonfiction recommendation '{title}'.")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ Not deleted — {e}")
        return False
    finally:
        con.close()


def set_nonfiction_done(title, done=True, user_id=None):
    """Mark a nonfiction recommendation done / not-done."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        row = con.execute("SELECT 1 FROM nonfiction_recommendations WHERE user_id=? AND title=?",
                          (uid, title)).fetchone()
        if not row:
            raise ValidationError(f"No nonfiction recommendation titled '{title}'.")
        _backup_once()
        con.execute("UPDATE nonfiction_recommendations SET done=? WHERE user_id=? AND title=?",
                    (1 if done else 0, uid, title))
        con.commit()
        print(f"  ✓ Marked nonfiction '{title}' done={done}.")
        return True
    except ValidationError as e:
        con.rollback()
        print(f"  ✗ {e}")
        return False
    finally:
        con.close()


def update_nonfiction_queue(titles, user_id=None):
    """Replace the nonfiction read queue with this ordered list of titles (per tenant)."""
    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        _backup_once()
        con.execute("DELETE FROM nonfiction_read_queue WHERE user_id=?", (uid,))
        for pos, t in enumerate(titles, 1):
            con.execute("INSERT INTO nonfiction_read_queue (position,title,user_id) "
                        "VALUES (?,?,?)", (pos, t, uid))
        con.commit()
        print(f"  ✓ Nonfiction queue updated ({len(titles)} books).")
    finally:
        con.close()


# ===========================================================================
# METADATA EDIT  — change an already-ranked book's non-score fields.
# ===========================================================================
# Scores have change_rating / change_nonfiction_rating; this is the equivalent
# for the METADATA columns (author, genre, series, series_number, words,
# year_read, and the title itself). It mirrors add_book's validation discipline
# and, like everything here, stores nothing derived — WA / ranks / tiers all
# recompute in the engine on the next read, so a genre change re-weights WA
# automatically (intended).

# Metadata columns editable per table. `books` and `nonfiction_books` carry the
# full set. `recommendations` (the TBR / prediction record) shares the identity
# and series columns but has NO year_read column, and its title is managed via
# its books row's rename cascade (below) rather than renamed in place — so the
# recommendations set is author/genre/series/series_number/words only. A field
# not listed for a table is rejected up front (never reaches the UPDATE).
_METADATA_FIELDS = ("title", "author", "genre", "series", "series_number",
                    "words", "year_read")
_TABLE_METADATA_FIELDS = {
    "books": _METADATA_FIELDS,
    "nonfiction_books": _METADATA_FIELDS,
    "recommendations": ("author", "genre", "series", "series_number", "words"),
}

# Title is the join key used elsewhere, so a rename must cascade. These are the
# tables (besides the row's own) that reference a book BY TITLE and are updated
# in the same transaction as the rename. Fiction: the prediction record in
# recommendations, the ordered read_queue, and the historical delta_log.
# Nonfiction: its TBR twins. A recommendations row is editable for metadata but
# is NOT renamed in place here (its title tracks the books row via the cascade
# above), so it has no cascade targets of its own. The Excel year sheets are NOT
# here — they live only in the import-only workbook, never in books.db, so
# they're outside the DB cascade (and the DB is the source of truth).
_TITLE_REF_TABLES = {
    "books": ["recommendations", "read_queue", "delta_log"],
    "nonfiction_books": ["nonfiction_recommendations", "nonfiction_read_queue"],
    "recommendations": [],
}


def update_book_metadata(current_title, table, fields, allow_new_genre=False, user_id=None):
    """
    Edit the METADATA (not scores) of an already-ranked book, with the same
    validation discipline as add_book. `table` is 'books', 'nonfiction_books',
    or 'recommendations' (the TBR / prediction record — no year_read column and
    no in-place rename there; see _TABLE_METADATA_FIELDS).
    `fields` is a dict; only the keys PRESENT are updated (partial update —
    omitted keys are left unchanged). Recognised keys: title, author, genre,
    series, series_number, words, year_read (subject to the per-table set).

    Validation (mirrors add_book):
      * genre (if given) must be in the matching genre_weights table unless
        allow_new_genre=True — a typo is REFUSED, never silently stored. For
        nonfiction the check only applies once nonfiction_genre_weights has
        genres (allow_new_genre parity with add_nonfiction_book).
      * year_read (if given) is range-checked 1900-2100.
      * series_number (if given) is normalised to int / float (0.5 prequels ok).
      * words (if given) must be a non-negative integer.
      * a title change must not collide with an existing row in `table`.

    TITLE RENAME cascades (option (a)): title is the join key used by the
    delta_log, read_queue and recommendations (fiction) / their nonfiction twins,
    so the rename is propagated to every one of those tables in the SAME
    transaction. The returned report lists how many rows each cascade touched.

    Nothing derived is stored, so WA / ranks / tiers recompute from the new
    values on the next engine read (a genre change re-weights WA — intended).
    Backs up the DB once per session before writing. Nothing commits on failure.

    Returns a report dict:
      {"ok": bool, "updated": {field: value}, "renamed_to": str|None,
       "cascade": {table: rows}, "error": str|None}
    """
    if table not in _TITLE_REF_TABLES:
        return {"ok": False, "updated": {}, "renamed_to": None, "cascade": {},
                "error": f"Unknown table '{table}'. Use 'books', "
                         f"'nonfiction_books', or 'recommendations'."}

    con = _connect()
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        allowed = _TABLE_METADATA_FIELDS[table]
        unknown = [k for k in fields if k not in allowed]
        if unknown:
            raise ValidationError(
                f"Field(s) not editable on {table}: {unknown}. "
                f"Valid for {table}: {list(allowed)}")

        row = con.execute(f"SELECT 1 FROM {table} WHERE user_id=? AND title=?",
                          (uid, current_title)).fetchone()
        if not row:
            raise ValidationError(f"No book titled '{current_title}' in {table}.")

        updates = {}  # column -> value to SET on the row

        # ── genre ── validated exactly like add_book / add_nonfiction_book ──
        if "genre" in fields:
            genre = fields["genre"]
            genre = None if genre is None else str(genre).strip() or None
            if genre is not None:
                if table in ("books", "recommendations"):
                    valid = _valid_genres(con, uid)
                    enforce = valid  # fiction always has a populated table
                else:
                    valid = _valid_nonfiction_genres(con, uid)
                    enforce = valid if valid else None  # empty → skip check
                if enforce is not None and genre not in enforce and not allow_new_genre:
                    weights_tbl = ("genre_weights"
                                   if table in ("books", "recommendations")
                                   else "nonfiction_genre_weights")
                    raise ValidationError(
                        f"Genre '{genre}' is not in {weights_tbl}. Fix the "
                        f"spelling (valid genres: {sorted(enforce)}) or pass "
                        f"allow_new_genre=True and add weights for it.")
            updates["genre"] = genre

        # ── year_read ── range-checked, same rule as set_year_read ──
        if "year_read" in fields:
            yr = fields["year_read"]
            if yr is None or str(yr).strip() == "":
                updates["year_read"] = None
            else:
                if not (1900 <= int(yr) <= 2100):
                    raise ValidationError(f"Year {yr} is out of range (1900-2100).")
                updates["year_read"] = int(yr)

        # ── series_number ── normalised like set_series_number ──
        if "series_number" in fields:
            sn = fields["series_number"]
            if sn is None or str(sn).strip() == "":
                updates["series_number"] = None
            else:
                snf = float(sn)
                updates["series_number"] = int(snf) if snf == int(snf) else snf

        # ── words ── non-negative integer ──
        if "words" in fields:
            w = fields["words"]
            if w is None or str(w).strip() == "":
                updates["words"] = None
            else:
                wi = int(w)
                if wi < 0:
                    raise ValidationError(f"Words {wi} cannot be negative.")
                updates["words"] = wi

        # ── author / series ── free text, empty → NULL ──
        for k in ("author", "series"):
            if k in fields:
                v = fields[k]
                updates[k] = (str(v).strip() or None) if v is not None else None

        # ── title (rename — special, cascaded below) ──
        new_title = None
        if "title" in fields:
            nt = (fields["title"] or "").strip()
            if not nt:
                raise ValidationError("New title cannot be empty.")
            if nt != current_title:
                dup = con.execute(f"SELECT 1 FROM {table} WHERE user_id=? AND title=?",
                                  (uid, nt)).fetchone()
                if dup:
                    raise ValidationError(
                        f"A book titled '{nt}' already exists in {table} — "
                        f"pick a different title.")
                new_title = nt
                updates["title"] = nt

        if not updates:
            raise ValidationError("No metadata fields to update.")

        _backup_once()
        set_clause = ",".join(f'"{c}"=?' for c in updates)
        con.execute(f"UPDATE {table} SET {set_clause} WHERE user_id=? AND title=?",
                    list(updates.values()) + [uid, current_title])

        # Cascade the rename to every table that references this book by title.
        cascade = {}
        if new_title is not None:
            for ref in _TITLE_REF_TABLES[table]:
                cur = con.execute(f"UPDATE {ref} SET title=? WHERE user_id=? AND title=?",
                                  (new_title, uid, current_title))
                if cur.rowcount:
                    cascade[ref] = cur.rowcount

        con.commit()

        shown = {k: v for k, v in updates.items() if k != "title"}
        changed = ", ".join(f"{k}={v!r}" for k, v in shown.items()) or "(title only)"
        print(f"  ✓ Updated metadata for '{current_title}' in {table}: {changed}")
        if new_title is not None:
            tail = (" — cascaded to " +
                    ", ".join(f"{t} ({n})" for t, n in cascade.items())
                    if cascade else " — no other tables referenced it")
            print(f"    -> renamed to '{new_title}'{tail}")
        return {"ok": True, "updated": shown, "renamed_to": new_title,
                "cascade": cascade, "error": None}
    except (ValidationError, TypeError, ValueError) as e:
        con.rollback()
        print(f"  ✗ Not updated — {e}")
        return {"ok": False, "updated": {}, "renamed_to": None, "cascade": {},
                "error": str(e)}
    finally:
        con.close()


# Create the nonfiction tables on import (idempotent), same discipline as the
# fiction schema-ensure calls near the top of this module.
_ensure_nonfiction_schema()

# read_month spans BOTH the fiction (books) and nonfiction (nonfiction_books)
# tables, so it must run after the nonfiction schema exists (above).
_ensure_read_month()


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
