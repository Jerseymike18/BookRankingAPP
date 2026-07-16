#!/usr/bin/env python3
"""
backfill_recommendation_words.py — one-time backfill of missing word counts on
existing recommendation (TBR) rows.

Recommendations saved before the lazy word-count backfill
(research_predict._entry_words) carry words=NULL whenever their research-cache entry
predated the `words` field (old batch-reference rows store only scores/conf — e.g.
every main-sequence Malazan book). The live fix only fills `words` on NEW predictions,
so already-saved rows stay blank. This script fills them.

For each recommendation with words IS NULL it resolves the count via the SAME path
research_book uses — the research cache (reusing any lazy backfill), then the durable
store, then a single small estimate call — and writes it through
db_write.update_book_metadata (the only sanctioned recommendations-metadata write).

It respects the configured DB backend, so to fix the hosted app run it against the
live Postgres, e.g.:

    DB_BACKEND=postgres DATABASE_URL="<supabase session-pooler URL>" \
        python3 backfill_recommendation_words.py --user <supabase-user-id>

Idempotent — only touches rows where words IS NULL. Start with --dry-run (no writes,
no estimate spend) to see the scope.

Usage:
    python3 backfill_recommendation_words.py --dry-run           # plan only, all users
    python3 backfill_recommendation_words.py --user <uid>        # backfill one tenant
    python3 backfill_recommendation_words.py --limit 50          # cap the writes
    python3 backfill_recommendation_words.py                     # backfill all users
"""
import argparse

import db_backend
import db_write
import research_layer as rl
import research_predict as rp


def resolve_words(title, author, genre, client, cache):
    """Return a word count using the same resolution research_book uses: a research-
    cache entry (lazily backfilled through _entry_words) -> the durable store -> a
    small estimate call. `client=None` (dry-run) disables the estimate, so only an
    already-known count resolves and everything else reports as needing an estimate."""
    e = rl.cache_lookup(cache, title)
    if e is not None:
        return rp._entry_words(e, title, author, genre, client)
    dur = rp.db_cache_get(rp.CACHE, title)
    if dur is not None and dur.get("words") is not None:
        return dur["words"]
    return rp.estimate_word_count(client, title, author, genre) if client is not None else None


def main():
    ap = argparse.ArgumentParser(description="Backfill missing word counts on recommendations.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report the plan without writing or spending on estimates.")
    ap.add_argument("--user", default=None, help="Limit to one user_id (default: all users).")
    ap.add_argument("--limit", type=int, default=None, help="Cap the number of rows updated.")
    args = ap.parse_args()

    client = None
    if not args.dry_run:
        try:
            client = rp.get_client()
        except Exception as ex:  # noqa: BLE001 — degrade to cache/durable-only fills
            print(f"! No Anthropic client ({ex}). Estimates disabled — only counts already "
                  f"in the cache/durable store will fill. Add a key or use --dry-run.")
    cache = rp.load_cache()

    con = db_backend.connect(db_write.DB)
    sql = "SELECT user_id, title, author, genre FROM recommendations WHERE words IS NULL"
    params = ()
    if args.user:
        sql += " AND user_id=?"
        params = (args.user,)
    rows = con.execute(sql, params).fetchall()
    con.close()

    scope = f"user {args.user}" if args.user else "all users"
    print(f"{len(rows)} recommendation row(s) with words IS NULL ({scope}).")
    if args.dry_run:
        print("DRY RUN — no writes, no estimate calls.")
    print()

    updated = skipped = failed = 0
    for uid, title, author, genre in rows:
        if args.limit is not None and updated >= args.limit:
            print(f"\nReached --limit {args.limit}; stopping.")
            break
        try:
            words = resolve_words(title, author, genre, client, cache)
        except Exception as ex:  # noqa: BLE001
            print(f"  FAIL   {title!r} — resolve error: {ex}")
            failed += 1
            continue

        if words is None:
            why = "needs an estimate (rerun without --dry-run)" if args.dry_run \
                else "estimate unavailable/failed"
            print(f"  SKIP   {title!r} — no count; {why}")
            skipped += 1
            continue

        if args.dry_run:
            print(f"  WOULD  {title!r} -> {words}")
            updated += 1
            continue

        try:
            rep = db_write.update_book_metadata(
                title, "recommendations", {"words": words}, user_id=uid)
        except Exception as ex:  # noqa: BLE001
            print(f"  FAIL   {title!r} — write error: {ex}")
            failed += 1
            continue
        if rep.get("ok"):
            print(f"  SET    {title!r} -> {words}")
            updated += 1
        else:
            print(f"  FAIL   {title!r} — {rep.get('error')}")
            failed += 1

    # Persist any lazy backfills into the file cache too (durable store was written
    # per-entry inside _entry_words). Dry runs and keyless runs leave the cache as-is.
    if not args.dry_run and client is not None:
        rp.save_cache(cache)

    verb = "would update" if args.dry_run else "updated"
    print(f"\nDone: {updated} {verb}, {skipped} skipped (no count), {failed} failed.")


if __name__ == "__main__":
    main()
