"""
migrate_finalize_tenancy.py — Phase 3 finalization (approach-a → hardened).
==========================================================================
Swap the Phase-2 placeholder user_id → Michael's REAL auth.users id, then (on
Postgres only) harden: user_id NOT NULL, FK → auth.users(id) ON DELETE CASCADE,
and the read-queue composite PK (user_id, position). Idempotent. Run per backend:

    # local SQLite (books.db) — swap only (no auth schema / FKs)
    MICHAEL_USER_ID=<uuid> .venv/bin/python migrate_finalize_tenancy.py
    # Supabase Postgres — swap + NOT NULL + FK + PK
    set -a; . ./.env; set +a
    MICHAEL_USER_ID=<uuid> DB_BACKEND=postgres .venv/bin/python migrate_finalize_tenancy.py

After this, db_backend.DEFAULT_USER_ID (the local single-user fallback) must be
Michael's real id — updated in code alongside this migration.
"""
import os
import db_backend

PLACEHOLDER = "00000000-0000-0000-0000-000000000001"
PER_USER = ["books", "recommendations", "nonfiction_books",
            "nonfiction_recommendations", "read_queue",
            "nonfiction_read_queue", "delta_log"]
QUEUE_TABLES = ["read_queue", "nonfiction_read_queue"]


def main():
    real = os.environ.get("MICHAEL_USER_ID", "").strip()
    if not real:
        raise SystemExit("ERROR: set MICHAEL_USER_ID to your real Supabase auth.users id.")
    pg = db_backend.backend() == "postgres"
    con = db_backend.connect()
    print(f"backend={db_backend.backend()}  swap {PLACEHOLDER} -> {real}")

    # 1) Swap placeholder -> real id (both backends). Controlled constants -> inline-safe.
    for t in PER_USER:
        cur = con.execute(
            f"UPDATE {t} SET user_id = '{real}' WHERE user_id = '{PLACEHOLDER}'")
        con.commit()
        print(f"  {t}: swapped {cur.rowcount} row(s)")

    if pg:
        # 2) NOT NULL now that every row is tagged.
        for t in PER_USER:
            con.execute(f'ALTER TABLE {t} ALTER COLUMN user_id SET NOT NULL')
            con.commit()
        print("  NOT NULL set on all 7 tables")

        # 3) Read-queue composite PK (position was per-DB; now per-tenant).
        for t in QUEUE_TABLES:
            try:
                con.execute(f'ALTER TABLE {t} DROP CONSTRAINT IF EXISTS {t}_pkey')
                con.execute(f'ALTER TABLE {t} ADD PRIMARY KEY (user_id, position)')
                con.commit()
                print(f"  {t}: PK -> (user_id, position)")
            except Exception as exc:
                con.rollback()
                print(f"  {t}: PK change skipped — {exc}")

        # 4) FK -> auth.users(id), cascade on user delete.
        for t in PER_USER:
            try:
                con.execute(
                    f'ALTER TABLE {t} ADD CONSTRAINT fk_{t}_user '
                    f'FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE')
                con.commit()
                print(f"  {t}: FK -> auth.users added")
            except Exception as exc:
                con.rollback()
                print(f"  {t}: FK skipped — {exc}")

    print("\nVerification:")
    ok = True
    for t in PER_USER:
        real_n = con.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id='{real}'").fetchone()[0]
        ph_n = con.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id='{PLACEHOLDER}'").fetchone()[0]
        if ph_n:
            ok = False
        print(f"  [{'OK ' if ph_n == 0 else 'LEFT'}] {t:<28} real={real_n}  placeholder={ph_n}")
    con.close()
    print("\nPlaceholder fully retired." if ok else "\nWARNING: placeholder rows remain.")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
