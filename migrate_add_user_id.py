"""
migrate_add_user_id.py — Phase 2 schema migration (multi-tenancy).
==================================================================
Add a nullable `user_id` to the seven per-user tables, backfill existing rows
with DEFAULT_USER_ID (Michael's placeholder), and index (user_id). Idempotent.
Runs against whichever backend DB_BACKEND selects — run once per backend:

    # local SQLite (books.db)
    .venv/bin/python migrate_add_user_id.py
    # Supabase Postgres
    set -a; . ./.env; set +a; DB_BACKEND=postgres .venv/bin/python migrate_add_user_id.py

approach (a): user_id is NULLABLE, no FK, no NOT NULL yet. Phase 3 swaps the
placeholder for Michael's real auth.users.id, then adds NOT NULL + FK. The
read_queue / nonfiction_read_queue position PK is left single-user for now; the
composite (user_id, position) PK is a Phase-3 task (before a 2nd user has a queue).

Global (NOT scoped — this IS the shared cold-start prior): genre_weights,
gcomp_weights, nonfiction_{genre,gcomp}_weights, component_corrections.
"""
import db_backend

PER_USER_TABLES = [
    "books", "recommendations", "nonfiction_books", "nonfiction_recommendations",
    "read_queue", "nonfiction_read_queue", "delta_log",
]


def _uid_type():
    return "UUID" if db_backend.backend() == "postgres" else "TEXT"


def main():
    placeholder = db_backend.DEFAULT_USER_ID
    con = db_backend.connect()
    print(f"backend={db_backend.backend()}  placeholder={placeholder}")
    for t in PER_USER_TABLES:
        cols = db_backend.table_columns(con, t)
        if "user_id" not in cols:
            con.execute(f"ALTER TABLE {t} ADD COLUMN user_id {_uid_type()}")
            print(f"  {t}: added user_id {_uid_type()}")
        else:
            print(f"  {t}: user_id already present")
        # placeholder is a controlled constant (not user input) -> safe to inline,
        # which also sidesteps text->uuid param adaptation differences per backend.
        cur = con.execute(
            f"UPDATE {t} SET user_id = '{placeholder}' WHERE user_id IS NULL")
        con.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_user_id ON {t}(user_id)")
        con.commit()
        print(f"    backfilled {cur.rowcount} null row(s); index ensured")

    print("\nVerification (every per-user row must be tagged, no NULLs):")
    ok = True
    for t in PER_USER_TABLES:
        total = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        tagged = con.execute(
            f"SELECT COUNT(*) FROM {t} WHERE user_id = '{placeholder}'").fetchone()[0]
        nulls = con.execute(
            f"SELECT COUNT(*) FROM {t} WHERE user_id IS NULL").fetchone()[0]
        if nulls:
            ok = False
        print(f"  [{'OK ' if nulls == 0 else 'NULLS'}] {t:<28} tagged={tagged}/{total}  nulls={nulls}")
    con.close()
    print("\nAll per-user rows tagged." if ok else "\nWARNING: some rows still NULL.")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
