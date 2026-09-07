#!/usr/bin/env python3
"""
mask_stored_worldbuilding.py — one-off repair: zero the worldbuilding components
on stored predictions for genres that have no worldbuilding.

WHY THIS EXISTS
---------------
`research_predict.mask_worldbuilding` stops the predict path INVENTING a
Depth2/Integration/Originality for a genre whose Worldbuilding category weight is
0 (Literary Fiction, Russian Literature, Gothic Fiction, Historical Fiction, …).
It fixes every prediction made from here on. It does nothing about the rows
already written: on the reference library, 119 of the 134 `recommendations` in
worldbuilding-free genres carry a confident, invented worldbuilding score, shown
on the read-queue card and destined to be logged as an ~8-point "miss" against
an actual of 0 the day the book is read.

This pass rewrites exactly those three columns on exactly those rows, to the
same 0.0 "no worldbuilding" sentinel the `books` table already stores.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
  * It does not touch `books`. A rated row is the reader's own judgement, not a
    prediction, and one book (Station 11, Speculative Literary Fiction) genuinely
    carries worldbuilding scores against its genre's weight. Whether that book's
    scores or that genre's weight is the odd one out is the owner's call — the
    data lint now WARNs about it rather than this script silently deciding.
  * It does not touch a row whose worldbuilding columns are already NULL. In
    `recommendations` NULL means "saved with no prediction at all" (a series
    bulk-add), which is a different state from "has no worldbuilding".
  * It writes nothing but those three columns, and only through
    `db_write.update_recommendation_scores` (HARD CONSTRAINT 2). Every other
    component, and every other column, is read back and written unchanged.
  * It logs no `delta_log` row. These are not re-predictions — no baseline moved
    and no model ran; the invented number is simply being withdrawn.

WA IS UNCHANGED, for the same reason the mask leaves it unchanged: a genre with
a Worldbuilding weight of 0 contributes nothing from that category to the WA
roll-up whatever its component values are. Nothing reorders. The script verifies
that per row and refuses to write if it is ever untrue.

USAGE
-----
    python3 scripts/mask_stored_worldbuilding.py              # dry run (default)
    python3 scripts/mask_stored_worldbuilding.py --write      # apply
    python3 scripts/mask_stored_worldbuilding.py --write --user-id <uuid>

Runs against whatever `db_write.DB` / DB_BACKEND resolves to — the local
books.db by default, the live Postgres when DATABASE_URL + DB_BACKEND=postgres
are set. It is idempotent: a second run finds nothing to do.
"""
import os
import sys
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import db_backend
import db_write
import db_loader
import research_predict as rp

WB = list(rp.WB_COMPONENTS)


def find_rows(uid):
    """Every recommendation whose genre has no Worldbuilding weight but which
    stores a non-zero worldbuilding component. Returns a list of
    (title, genre, {component: value}) with the FULL 14-component vector."""
    con = db_backend.connect(db_write.DB, readonly=True)
    try:
        zero_genres = {r[0] for r in con.execute(
            "SELECT genre FROM genre_weights WHERE COALESCE(worldbuilding, 0) = 0")}
        cols = ", ".join(f'"{c}"' for c in db_write.FICTION_COMPONENTS)
        rows = con.execute(
            f"SELECT title, genre, {cols} FROM recommendations "
            f"WHERE user_id=? ORDER BY title", (uid,)).fetchall()
    finally:
        con.close()

    out = []
    for title, genre, *vals in rows:
        if genre not in zero_genres:
            continue
        scores = dict(zip(db_write.FICTION_COMPONENTS, vals))
        if all(scores[c] is None for c in WB):
            continue                      # no prediction stored at all
        if not any(scores[c] for c in WB):
            continue                      # already masked
        out.append((title, genre, scores))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--write", action="store_true",
                    help="apply the change (default is a dry run)")
    ap.add_argument("--user-id", default=None,
                    help="tenant to repair (default: DEFAULT_USER_ID)")
    args = ap.parse_args(argv)

    uid = args.user_id or db_backend.DEFAULT_USER_ID
    backend = db_backend.backend()
    print(f"backend: {backend}   db: {db_write.DB}   tenant: {uid}")

    rows = find_rows(uid)
    if not rows:
        print("\nNothing to do — no stored prediction invents worldbuilding.")
        return 0

    # WA must not move. Roll each row up both ways with the reader's EFFECTIVE
    # weights and refuse the whole pass if any book would reorder.
    _books, gw, gcw = db_loader.load_from_db(db_write.DB, user_id=uid)
    worst = 0.0
    for title, genre, scores in rows:
        masked = rp.mask_worldbuilding(scores, genre, gw)
        before = rp._wa_from_components(scores, genre, gw, gcw)
        after = rp._wa_from_components(masked, genre, gw, gcw)
        worst = max(worst, abs(before - after))
    if worst > 1e-9:
        print(f"\nREFUSING: masking would move a WA by {worst:.6f}. That should be "
              f"impossible for a genre with a Worldbuilding weight of 0 — "
              f"investigate the weights before running this.")
        return 1

    by_genre: dict[str, int] = {}
    for _t, g, _s in rows:
        by_genre[g] = by_genre.get(g, 0) + 1

    print(f"\n{len(rows)} recommendation(s) carry an invented worldbuilding score:")
    for g, n in sorted(by_genre.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>4}  {g}")
    print(f"\nmax |ΔWA| across all of them: {worst:.2e}  (nothing reorders)")
    print("\nsample:")
    for title, genre, scores in rows[:8]:
        vals = ", ".join(f"{scores[c]:.2f}" for c in WB)
        print(f"  {title[:48]:<48} {genre[:26]:<26} {vals} → 0, 0, 0")
    if len(rows) > 8:
        print(f"  … and {len(rows) - 8} more")

    if not args.write:
        print("\nDRY RUN — nothing written. Re-run with --write to apply.")
        return 0

    ok = failed = 0
    for title, genre, scores in rows:
        masked = rp.mask_worldbuilding(scores, genre, gw)
        if db_write.update_recommendation_scores(title, masked, user_id=uid):
            ok += 1
        else:
            failed += 1
            print(f"  ✗ {title}")
    print(f"\nrewrote {ok} row(s)" + (f", {failed} FAILED" if failed else ""))

    left = find_rows(uid)
    print("verified: none left." if not left
          else f"WARNING: {len(left)} row(s) still unmasked.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
