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

Wired in as of Phase 1: the whole DB layer connects through connect(). Phase 2
adds DEFAULT_USER_ID for per-tenant scoping (see below).

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

import contextlib
import os
import sqlite3
import threading
import time

# Default sqlite path — mirrors the existing `DB = "books.db"` constant, resolved
# relative to cwd (backend/main.py chdirs to project root before any connect).
SQLITE_PATH = os.environ.get("SQLITE_PATH", "books.db")

# Tenancy. Every per-user table carries a user_id; callers that don't supply one
# fall back to DEFAULT_USER_ID — the local single-user (Michael) fallback for
# sqlite dev and any not-yet-auth'd path. Phase 3 overrides it per-request from
# the verified Supabase JWT. As of Phase-3 finalization this is Michael's REAL
# auth.users id (the Phase-2 placeholder 00000000-…-0001 was swapped out via
# migrate_finalize_tenancy.py).
DEFAULT_USER_ID = os.environ.get(
    "DEFAULT_USER_ID", "e3160346-91f8-4334-a099-202217b376a5")


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


# --------------------------------------------------------------------------
# Postgres connection pool (latency only; sqlite path untouched).
# --------------------------------------------------------------------------
# Every connect() used to open a brand-new server connection — a full
# TCP+TLS+auth round trip from Railway to the Supabase pooler on EVERY query
# helper (a single repredict pass makes ~17 of them). Pooling reuses live
# server connections across requests. Semantics are preserved:
#   * each borrowed connection is health-checked (SELECT 1) so a server-side
#     drop (pooler idle timeout, redeploy) is replaced, not surfaced — this is
#     what makes retaining idle connections safe;
#   * PgConnection.close() ROLLS BACK then returns the connection to the pool,
#     so no transaction state or uncommitted write ever leaks to the next
#     borrower — exactly what the old close() discarded;
#   * any pool failure falls back to the old one-fresh-connection path.
# Kill switch: DB_POOL=0 restores the prior connect-per-call behavior.
_PG_POOL = None
_PG_POOL_LOCK = threading.Lock()
# ---------------------------------------------------------------------------
# CONNECTION BUDGET — this is a HARD ceiling, and exceeding it is an OUTAGE.
# ---------------------------------------------------------------------------
# The Supabase SESSION pooler caps total clients. Measured 2026-08-21:
#
#   FATAL: (EMAXCONNSESSION) max clients reached in session mode
#          - max clients are limited to pool_size: 15
#
# That is not backpressure — it is a refused connection, so once the app is at the
# cap EVERY request fails, including the ones that would have been served from a
# warm pool. So the budget is derived here rather than guessed, and it is derived
# for the WORST moment, not the steady state.
#
# The worst moment is a redeploy: Railway starts the new container before the old
# one exits, so both hold their connections at once. Budgeting a container to half
# the pooler limit is what makes that overlap survivable.
#
# Per container, at rest:
#     WORKERS               cache_sync listeners (one per worker, long-lived)
#   + WORKERS * DB_POOL_MAX query connections
# The advisory-lock connections are deliberately NOT counted: CrossProcessLock now
# closes its connection on release, so it is transient rather than parked.
DB_MAX_CLIENTS = int(os.environ.get("DB_MAX_CLIENTS", "15"))
_WORKERS = max(1, int(os.environ.get("WEB_CONCURRENCY", "1")))
# Half the pooler, so two overlapping containers still fit.
_CONTAINER_BUDGET = max(2, DB_MAX_CLIENTS // 2)
# What is left for query pools once every worker's listener is reserved.
_QUERY_BUDGET = max(_WORKERS, _CONTAINER_BUDGET - _WORKERS)
DB_POOL_MAX = max(1, int(os.environ.get("DB_POOL_MAX", "0")) or
                  _QUERY_BUDGET // _WORKERS)
# Connections the pool RETAINS. psycopg2's putconn keeps one only while
# `len(pool) < minconn`, so minconn=0 means it pools NOTHING (see _get_pg_pool).
# Retaining the whole ceiling is what makes a warm request cost no handshake.
DB_POOL_MIN = max(1, int(os.environ.get("DB_POOL_MIN", str(DB_POOL_MAX))))


def _pool_enabled():
    return os.environ.get("DB_POOL", "1") != "0"


def _get_pg_pool():
    global _PG_POOL
    if _PG_POOL is None:
        with _PG_POOL_LOCK:
            if _PG_POOL is None:
                from psycopg2 import pool as _pgpool
                # See the CONNECTION BUDGET block above for how the sizes are
                # derived — exceeding the pooler's client cap is an outage, not a
                # slowdown.
                #
                # minconn MUST NOT be 0. psycopg2's putconn keeps a returned
                # connection only `if len(self._pool) < self.minconn` — with
                # minconn=0 that is never true, so every putconn CLOSED the
                # connection and the "pool" pooled nothing. Every connect() was a
                # fresh TCP+TLS+auth handshake to Supabase: measured at ~234ms
                # against a 28ms round trip, on a path every endpoint takes one to
                # three times per request. Setting minconn = maxconn makes the
                # condition true until the pool is full, i.e. connections are
                # actually retained. A retained connection that the server or the
                # pooler has since dropped is caught by the SELECT 1 health check
                # in _borrow_pg and transparently replaced.
                _PG_POOL = _pgpool.ThreadedConnectionPool(
                    DB_POOL_MIN, DB_POOL_MAX, _database_url())
    return _PG_POOL


# When a pooled connection was last returned after a SUCCESSFUL use. A connection
# handed back seconds ago is alive with near-certainty, so re-proving it with a
# SELECT 1 spends a whole round trip to Supabase on every borrow — and endpoints
# borrow one to three times per request. Past HEALTH_TTL_S we check again, which is
# what still catches a pooler idle-timeout or a server restart. Supabase's pooler
# idles connections out on the order of minutes, so 30s is comfortably conservative.
_last_ok = {}
HEALTH_TTL_S = float(os.environ.get("DB_HEALTH_TTL_S", "30"))


def _borrow_pg():
    """A healthy raw psycopg2 connection from the pool (health-checked; one
    stale connection is replaced transparently). Raises on pool exhaustion or
    connect failure — the caller falls back to an unpooled connection."""
    pool = _get_pg_pool()
    raw = pool.getconn()
    try:
        if time.time() - _last_ok.get(id(raw), 0.0) < HEALTH_TTL_S:
            return raw, pool    # verified recently — skip the round trip
        cur = raw.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
        raw.rollback()          # leave the borrowed conn transaction-clean
        _last_ok[id(raw)] = time.time()
        return raw, pool
    except Exception:
        try:
            pool.putconn(raw, close=True)   # discard the dead connection
        except Exception:
            pass
        return pool.getconn(), pool         # one fresh retry; errors propagate


# ---------------------------------------------------------------------------
# READ-ONLY SCOPE — autocommit for paths that only read
# ---------------------------------------------------------------------------
# A pooled connection is returned with a rollback(), so the NEXT borrower's first
# statement has to open a transaction before it can run. Measured against Supabase:
#
#     SELECT 1 with a transaction already open      23.7ms   (1 round trip)
#     SELECT 1 after a rollback (needs BEGIN)       72.1ms   (~3 round trips)
#     SELECT 1 on an autocommit connection          24.8ms   (1 round trip)
#
# So every query was paying roughly three round trips to do the work of one — and a
# round trip to this database costs ~45ms from Railway. For a READ that transaction
# buys nothing: there is no atomicity to protect.
#
# This is scoped rather than global on purpose. Flipping the pool to autocommit
# wholesale would silently break the multi-statement writers (update_queue rewrites
# the whole queue, the metadata writers cascade across tables) — each statement
# would commit on its own and a mid-way failure would leave a half-applied change.
# So callers OPT IN around code they know only reads:
#
#     with db_backend.readonly():
#         books, gw, gcw = db_loader.load_from_db(...)
#
# Scoping it in the CALLER also keeps the read-only engine files untouched — the
# loader is on CLAUDE.md's do-not-modify list, and nothing about it changes here
# anyway: same SQL, same rows, same numbers, only the transaction mode.
#
# WRITING inside a readonly() scope is a bug: the write would still succeed, but
# statement-by-statement with no rollback. Wrap reads only.
_readonly_state = threading.local()


def in_readonly():
    return getattr(_readonly_state, "on", False)


@contextlib.contextmanager
def readonly():
    """Mark this thread's connections as read-only (autocommit) for the duration.

    Re-entrant, and thread-scoped rather than global because FastAPI serves sync
    handlers on a threadpool — a global flag would leak one request's read mode
    onto another request's write."""
    prev = in_readonly()
    _readonly_state.on = True
    try:
        yield
    finally:
        _readonly_state.on = prev


def connect(path=None, uri=False, readonly=False):
    """Return a DB connection for the configured backend.

    sqlite   -> a genuine sqlite3.Connection (unchanged behavior). `path` overrides
                the default db file (callers pass db_write.DB / a test-db path so the
                test_engine monkeypatch keeps working); `uri` passes through to
                sqlite3.connect(..., uri=True) for read-only "file:...?mode=ro" opens.
    `readonly=True` is the inline form of the readonly() scope above — use it at a
    call site that only SELECTs, to skip the transaction its query would otherwise
    have to open. Ignored on sqlite. NEVER pass it on a path that writes.

    postgres -> a PgConnection proxy that speaks the sqlite3 surface the app uses,
                backed by a pooled server connection (see above; DB_POOL=0 for a
                fresh connection per call). `path`/`uri` are sqlite-only and
                ignored (the DSN is DATABASE_URL).
    """
    b = backend()
    if b == "sqlite":
        if uri:
            return sqlite3.connect(path, uri=True)
        return sqlite3.connect(path or SQLITE_PATH)
    if b == "postgres":
        try:
            import psycopg2  # noqa: F401  (lazy: only needed in postgres mode)
        except ImportError as e:  # pragma: no cover - env-dependent
            raise RuntimeError(
                "DB_BACKEND=postgres requires psycopg2. Install it with "
                "`pip install psycopg2-binary`."
            ) from e
        if _pool_enabled():
            try:
                raw, pool = _borrow_pg()
                return PgConnection(raw=raw, pool=pool,
                                    autocommit=readonly or in_readonly())
            except Exception:
                pass                        # pool trouble -> unpooled fallback
        return PgConnection(_database_url(), autocommit=readonly or in_readonly())
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
    relies on. Only instantiated in postgres mode.

    Backed either by a POOLED raw connection (`raw` + `pool` — close() returns
    it to the pool after a rollback) or, when constructed with a `dsn`, by a
    private connection that close() really closes (the pre-pool behavior and
    the DB_POOL=0 / pool-failure fallback)."""

    def __init__(self, dsn=None, raw=None, pool=None, autocommit=False):
        if raw is None:
            import psycopg2
            raw = psycopg2.connect(dsn)
        self._conn = raw
        self._pool = pool
        self._returned = False
        # Read-only scope: run in autocommit so a plain SELECT costs one round trip
        # instead of BEGIN + query (see readonly()). Setting the attribute is local
        # — psycopg2 applies it to the next transaction — so this costs nothing. It
        # raises if a transaction is somehow already open, in which case we simply
        # stay transactional rather than fail the request.
        self._autocommit = False
        if autocommit:
            try:
                raw.autocommit = True
                self._autocommit = True
            except Exception:
                pass
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
        if self._returned:
            return                          # idempotent, like sqlite3.close()
        self._returned = True
        if self._pool is None:
            self._conn.close()
            return
        try:
            if self._autocommit:
                # Nothing to roll back (that is the point), but the mode MUST be
                # cleared before the connection goes back in the pool, or the next
                # borrower — possibly a multi-statement writer — would silently run
                # without a transaction.
                try:
                    self._conn.autocommit = False
                except Exception:
                    self._conn.rollback()
            else:
                self._conn.rollback()       # never park a conn mid-transaction
            self._pool.putconn(self._conn)
            # It just served a request without erroring, so the next borrow within
            # HEALTH_TTL_S can skip its SELECT 1 (see _borrow_pg).
            _last_ok[id(self._conn)] = time.time()
        except Exception:
            _last_ok.pop(id(self._conn), None)
            try:
                _last_ok.pop(id(self._conn), None)
                self._pool.putconn(self._conn, close=True)
            except Exception:
                try:
                    self._conn.close()
                except Exception:
                    pass

    def __del__(self):
        # Safety net for call sites that error before reaching close(): return
        # the pooled slot rather than leaking it until pool exhaustion.
        try:
            self.close()
        except Exception:
            pass

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
