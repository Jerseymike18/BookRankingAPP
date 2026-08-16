"""
refresh_from_live.py — pull the LIVE hosted data down into local `books.db`
===========================================================================
The hosted app (Railway + Supabase Postgres) and the committed `books.db`
(SQLite) are two separate stores with no sync between them — a deliberate
consequence of the 2026-07-12 decision that the live app is the sole source of
truth. So `books.db` freezes at its last *local* edit while the live library
grows, and everything derived from the file — local dev, the static showcase,
any offline analysis — silently reports stale numbers.

This is the one-way repair: **Postgres -> books.db, live always wins.**

WHY THE DIRECTION IS LOAD-BEARING
  The inverse (`migrate_sqlite_to_postgres.py`, the Phase-1 migration) pushes the
  local file INTO Postgres. Running that today would overwrite live edits with a
  stale copy — silent data loss on the only authoritative store. This script can
  never do that: the Postgres session is opened READ ONLY, so a stray write is
  refused by the server, not merely by convention.

TENANT SCOPING IS A PRIVACY CONTROL, NOT A FILTER
  `books.db` is what the public static showcase is built from
  (scripts/export_static_data.py). Copying every tenant's rows into it would put
  other people's libraries into a publicly published artifact. So tenant tables
  are pulled for ONE user id only (default: db_backend.DEFAULT_USER_ID, the
  owner). Global tables (the weight tables, component_corrections) carry no
  user_id and are copied whole.

WHAT IT TOUCHES
  Row data only, into the EXISTING local schema. No DDL: no CREATE, no DROP, no
  ALTER. A table present locally but absent upstream is reported and skipped, not
  emptied. Only columns present in BOTH ends are copied; anything else is
  reported so a schema drift can't vanish silently.

  This is a bulk restore, in the same category as the committed `migrate_*.py`
  tools — deliberately NOT going through `db_write` (whose validators are for
  app-level single-row writes, not for reproducing an authoritative store).

USAGE
    python3 scripts/refresh_from_live.py               # DRY RUN (default): diff only
    python3 scripts/refresh_from_live.py --write       # apply, after backing up
    python3 scripts/refresh_from_live.py --user <uuid> # a different tenant

DATABASE_URL comes from the environment, else from the project-root `.env`. It is
never printed, never logged, and never committed.
"""

import argparse
import datetime as dt
import os
import shutil
import sqlite3
import sys
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SQLITE = os.path.join(ROOT, "books.db")


def load_database_url():
    """DATABASE_URL from the environment, else parsed out of the project `.env`.
    Returns the DSN; never prints it. Nothing in this repo auto-loads `.env` and
    python-dotenv is not a dependency, so this is a deliberately tiny parser."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "DATABASE_URL":
                    return v.strip().strip('"').strip("'")
    raise SystemExit(
        "DATABASE_URL not set and not found in .env — cannot reach the live store."
    )


def sqlite_tables(con):
    """{table: [columns]} for the local file."""
    out = {}
    for (t,) in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ):
        out[t] = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
    return out


def pg_columns(pg, table):
    """Columns of `table` upstream, or None when the table does not exist."""
    cur = pg.cursor()
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
        (table,),
    )
    cols = [r[0] for r in cur.fetchall()]
    cur.close()
    return cols or None


def _clean(v):
    """Coerce a psycopg2 value into something sqlite3 can bind. Decimal appears if
    any column was ever created NUMERIC rather than DOUBLE PRECISION (the parity
    trap the Phase-1 migration documents); datetimes appear on timestamp columns."""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    return v


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true",
                    help="apply the refresh (default is a dry run that writes nothing)")
    ap.add_argument("--user", default=None,
                    help="tenant whose rows are pulled (default: db_backend.DEFAULT_USER_ID)")
    ap.add_argument("--sqlite", default=SQLITE, help="target file (default: ./books.db)")
    args = ap.parse_args()

    import db_backend
    uid = args.user or db_backend.DEFAULT_USER_ID

    try:
        import psycopg2
    except ImportError:
        raise SystemExit("psycopg2 is not installed — `pip install psycopg2-binary`.")

    dsn = load_database_url()
    # READ ONLY is the hard guarantee that this can never write upstream: the
    # SERVER refuses any INSERT/UPDATE/DELETE on this session, so a coding
    # mistake here cannot damage the authoritative store.
    pg = psycopg2.connect(dsn)
    pg.set_session(readonly=True, autocommit=True)

    con = sqlite3.connect(args.sqlite)
    local = sqlite_tables(con)

    plan, skipped = [], []
    for table, lcols in sorted(local.items()):
        pcols = pg_columns(pg, table)
        if pcols is None:
            skipped.append((table, "absent upstream"))
            continue
        common = [c for c in lcols if c in pcols]
        if not common:
            skipped.append((table, "no columns in common"))
            continue
        tenant = "user_id" in common
        cur = pg.cursor()
        qcols = ", ".join(f'"{c}"' for c in common)
        if tenant:
            cur.execute(f'SELECT {qcols} FROM "{table}" WHERE user_id = %s', (uid,))
        else:
            cur.execute(f'SELECT {qcols} FROM "{table}"')
        rows = [tuple(_clean(v) for v in r) for r in cur.fetchall()]
        cur.close()
        if tenant:
            n_local = con.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE user_id = ?', (uid,)).fetchone()[0]
        else:
            n_local = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        plan.append({"table": table, "cols": common, "rows": rows, "tenant": tenant,
                     "n_local": n_local, "n_live": len(rows),
                     "dropped": [c for c in lcols if c not in pcols],
                     "extra": [c for c in pcols if c not in lcols]})

    print(f"tenant: {uid}")
    print(f"target: {args.sqlite}\n")
    print(f"{'table':<34}{'local':>8}{'live':>8}{'delta':>8}   scope")
    for p in plan:
        d = p["n_live"] - p["n_local"]
        print(f"  {p['table']:<32}{p['n_local']:>8}{p['n_live']:>8}{d:>+8}   "
              f"{'tenant' if p['tenant'] else 'global'}")
    for p in plan:
        if p["dropped"]:
            print(f"  ! {p['table']}: local-only columns NOT refreshed: {p['dropped']}")
        if p["extra"]:
            print(f"  ! {p['table']}: upstream-only columns NOT copied: {p['extra']}")
    for t, why in skipped:
        print(f"  - {t}: skipped ({why})")

    total = sum(p["n_live"] - p["n_local"] for p in plan)
    if not args.write:
        print(f"\nDRY RUN — nothing written. Net row change would be {total:+d}.")
        print("Re-run with --write to apply.")
        pg.close(); con.close()
        return 0

    backup = f"{args.sqlite}.bak.{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(args.sqlite, backup)
    print(f"\nbackup: {backup}")

    try:
        for p in plan:
            t, cols = p["table"], p["cols"]
            # Tenant tables are cleared ONLY for this user, so any other tenant's
            # local rows (there should be none) are left alone rather than wiped.
            if p["tenant"]:
                con.execute(f'DELETE FROM "{t}" WHERE user_id = ?', (uid,))
            else:
                con.execute(f'DELETE FROM "{t}"')
            if p["rows"]:
                ph = ",".join("?" for _ in cols)
                qc = ", ".join(f'"{c}"' for c in cols)
                con.executemany(f'INSERT INTO "{t}" ({qc}) VALUES ({ph})', p["rows"])
        con.commit()
    except Exception:
        con.rollback()
        con.close()
        shutil.copy2(backup, args.sqlite)
        print("FAILED — books.db restored from the backup. Nothing changed.")
        raise

    print("\nverifying…")
    ok = True
    for p in plan:
        t = p["table"]
        n = con.execute(
            f'SELECT COUNT(*) FROM "{t}" WHERE user_id = ?', (uid,)).fetchone()[0] \
            if p["tenant"] else con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        if n != p["n_live"]:
            ok = False
            print(f"  MISMATCH {t}: {n} local vs {p['n_live']} live")
    con.close(); pg.close()
    print("  all table counts match live." if ok else "  VERIFY FAILED — see above.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
