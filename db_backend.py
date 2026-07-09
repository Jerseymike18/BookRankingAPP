"""
db_backend.py — the SQLite/Postgres connection switch (Phase 1 scaffold)
========================================================================
A single place that hands out a database connection, so the app can run on
local SQLite (the default, unchanged) OR Supabase Postgres, chosen by config.

    DB_BACKEND = "sqlite"  (default)  -> sqlite3.connect("books.db")
    DB_BACKEND = "postgres"           -> psycopg2.connect(DATABASE_URL)

Design goals
------------
* ZERO behavior change on the default (sqlite) path. In sqlite mode `connect()`
  returns a *real* `sqlite3.Connection` — no wrapper, no interception — so today's
  code is byte-identical. The Postgres proxy exists only when explicitly selected.
* Minimal-touch rewiring. The Postgres proxy mimics the exact `sqlite3` surface the
  codebase uses — connection-level `.execute()` / `.executemany()`, cursor
  iteration + `fetchone/fetchall`, `.commit()/.close()`, and a `.row_factory`
  hook — and translates `?`-style SQL to psycopg2's `%s` style at execute time,
  so **existing SQL strings stay unchanged**. Rewiring a call site is then just:
      sqlite3.connect(db_write.DB)   ->   db_backend.connect()

This module is NOT wired into anything yet (Phase 1 scaffold). Importing it has
no effect until call sites are switched over in the rewiring step.

----------------------------------------------------------------------------
REWIRING CHECKLIST (the bounded set of SQLite-isms found in the live code; each
is handled by this module OR is a one-line SQL edit at rewire time):

  [proxy] connection-level `con.execute(...)`      -> PgConnection.execute (28 sites)
  [proxy] `?` placeholders + literal `%`           -> _translate() at execute time
  [proxy] `con.executemany(...)`                   -> PgConnection.executemany
          (db_write.py:244, db_write.py:810)
  [proxy] `con.row_factory = sqlite3.Row`          -> RealDictCursor (dict rows)
          (repredict_on_add.py:337 — rows read AND mutated by name)
  [edit ] `PRAGMA table_info(<t>)`                 -> use table_columns() helper
          (db_write.py:73, db_write.py:157 — schema-introspection admin paths)
  [edit ] `ORDER BY rowid DESC`                    -> `ORDER BY id DESC`
          (backend/main.py:1479, backend/main.py:1844 — id aliases rowid here)

Not present (audited, nothing to do): connection-level `with` blocks,
INSERT OR REPLACE/IGNORE, ON CONFLICT, AUTOINCREMENT, lastrowid, executescript.
----------------------------------------------------------------------------
"""

import os
import sqlite3

# Default sqlite path — mirrors the existing `DB = "books.db"` constant, resolved
# relative to cwd (backend/main.py chdirs to project root before any connect).
SQLITE_PATH = os.environ.get("SQLITE_PATH", "books.db")


def backend():
    """Selected backend: 'sqlite' (default) or 'postgres'. Env-driven, no hardcoding."""
    return os.environ.get("DB_BACKEND", "sqlite").strip().lower()


def _database_url():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DB_BACKEND=postgres but DATABASE_URL is not set. "
            "Provide the Supabase Postgres connection string via the DATABASE_URL "
            "env var (never hardcoded)."
        )
    return url


def connect():
    """Return a DB connection for the configured backend.

    sqlite   -> a genuine sqlite3.Connection (unchanged behavior).
    postgres -> a PgConnection proxy that speaks the sqlite3 surface the app uses.
    """
    b = backend()
    if b == "sqlite":
        return sqlite3.connect(SQLITE_PATH)
    if b == "postgres":
        try:
            import psycopg2  # noqa: F401  (lazy: only needed in postgres mode)
        except ImportError as e:  # pragma: no cover - env-dependent
            raise RuntimeError(
                "DB_BACKEND=postgres requires psycopg2. Install it with "
                "`pip install psycopg2-binary`."
            ) from e
        return PgConnection(_database_url())
    raise ValueError(f"Unknown DB_BACKEND={b!r} (expected 'sqlite' or 'postgres').")


# --------------------------------------------------------------------------
# SQL paramstyle translation: SQLite qmark ('?') -> psycopg2 pyformat ('%s').
# --------------------------------------------------------------------------
def _translate(sql):
    """Translate one SQL string from sqlite3 qmark style to psycopg2 style.

    * '?' bind placeholders  -> '%s', but ONLY outside string literals (so a
      literal '?' inside quotes is never mangled).
    * every literal '%'      -> '%%' (psycopg2 treats '%' as its own format
      marker whenever bind params are supplied; doubling is collapsed back to a
      single '%' by psycopg2 during binding).

    Handles single- and double-quoted literals including the SQL '' escape.
    Pure function — see the __main__ self-test below.
    """
    out = []
    i, n = 0, len(sql)
    quote = None  # None | "'" | '"'  -> currently inside this kind of literal
    while i < n:
        ch = sql[i]
        if quote is not None:
            if ch == quote:
                # '' (or "") inside a literal is an escaped quote: stays inside.
                if i + 1 < n and sql[i + 1] == quote:
                    out.append(ch)
                    out.append(quote)
                    i += 2
                    continue
                quote = None
                out.append(ch)
            elif ch == "%":
                out.append("%%")
            else:
                out.append(ch)
        else:
            if ch in ("'", '"'):
                quote = ch
                out.append(ch)
            elif ch == "?":
                out.append("%s")
            elif ch == "%":
                out.append("%%")
            else:
                out.append(ch)
        i += 1
    return "".join(out)


class PgConnection:
    """A thin psycopg2 wrapper presenting the sqlite3.Connection surface the app
    relies on. Only instantiated in postgres mode."""

    def __init__(self, dsn):
        import psycopg2
        self._conn = psycopg2.connect(dsn)
        # Assign `sqlite3.Row` (truthy) to get dict-style rows, mirroring the one
        # `con.row_factory = sqlite3.Row` site. None -> plain tuple rows (default).
        self.row_factory = None

    def _new_cursor(self):
        if self.row_factory is not None:
            import psycopg2.extras as extras
            return self._conn.cursor(cursor_factory=extras.RealDictCursor)
        return self._conn.cursor()

    def execute(self, sql, params=None):
        """Connection-level execute (sqlite3 convenience). Returns a fresh cursor
        that supports fetchone()/fetchall()/iteration/rowcount — just like sqlite3."""
        cur = self._new_cursor()
        if params is None:
            cur.execute(sql)
        else:
            cur.execute(_translate(sql), params)
        return cur

    def executemany(self, sql, seq_of_params):
        cur = self._new_cursor()
        cur.executemany(_translate(sql), seq_of_params)
        return cur

    def cursor(self, *args, **kwargs):
        return self._conn.cursor(*args, **kwargs)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    # Context-manager parity with sqlite3 (commit on success, rollback on error,
    # do NOT close — matches sqlite3). No live `with con:` sites today, included
    # for safety.
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        return False


def table_columns(con, table):
    """Backend-aware replacement for `PRAGMA table_info(<table>)` where the code
    only needs the set/list of column names. Returns a list of column-name strings.

    Use this at rewire time in place of the two PRAGMA table_info sites."""
    if backend() == "postgres":
        rows = con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = ? ORDER BY ordinal_position",
            (table,),
        ).fetchall()
        return [r[0] for r in rows]
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]


if __name__ == "__main__":
    # Pure self-test of the paramstyle translator — no database needed.
    cases = [
        ("SELECT 1 FROM books WHERE title=?", "SELECT 1 FROM books WHERE title=%s"),
        ("INSERT INTO t (a,b) VALUES (?,?)", "INSERT INTO t (a,b) VALUES (%s,%s)"),
        # literal '%' in a LIKE pattern gets doubled for psycopg2
        ("SELECT * FROM d WHERE tag LIKE 'baseline_repredict:%'",
         "SELECT * FROM d WHERE tag LIKE 'baseline_repredict:%%'"),
        # '?' inside a string literal must NOT be treated as a placeholder
        ("SELECT '? literal' , x FROM t WHERE y=?",
         "SELECT '? literal' , x FROM t WHERE y=%s"),
        # escaped '' quote inside a literal
        ("SELECT 'it''s ok' FROM t WHERE z=?",
         "SELECT 'it''s ok' FROM t WHERE z=%s"),
        # no placeholders, no percent -> unchanged
        ("SELECT title FROM books ORDER BY id DESC LIMIT 1",
         "SELECT title FROM books ORDER BY id DESC LIMIT 1"),
    ]
    ok = True
    for src, want in cases:
        got = _translate(src)
        flag = "PASS" if got == want else "FAIL"
        if got != want:
            ok = False
        print(f"  [{flag}] {src!r}\n         -> {got!r}")
    print("\nALL TRANSLATOR CASES PASSED" if ok else "\nTRANSLATOR SELF-TEST FAILED")
    raise SystemExit(0 if ok else 1)
