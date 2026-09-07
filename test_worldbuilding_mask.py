"""
test_worldbuilding_mask.py — a genre with no worldbuilding gets no worldbuilding
score, on the PREDICT side as well as the read side.

WHY THIS EXISTS
---------------
Worldbuilding is optional in the scoring model (CLAUDE.md): nine of the sixteen
fiction genres carry a Worldbuilding CATEGORY weight of 0, and the rated library
agrees — every read book in Classical Drama, Classical Epic, Gothic Fiction,
Literary Fiction, Russian Literature (and the rest) stores 0.0 in all three
worldbuilding components.

`genre_affinity.py` has masked those three components on the READ side since it
shipped, because an unmasked component z-profile reports Literary Fiction at
-2.26 on them — which reads as "this reader hates its worldbuilding" when it
means "there is none to score".

The PREDICT side had no such mask. The research LLM scores all 14 components for
every book, and `correct_and_predict` passed them straight through, so a Literary
Fiction novel came back with a confident Depth2/Integration/Originality — shown
on the Predict card, persisted into `recommendations` on save, and later logged
against an actual of 0.0 as an ~8-point "miss" that is an artifact, not an error.
(reresearch_and_measure already calls those exact residuals "spurious ~8-pt
errors, not real signal" and drops them from the correction's training pool.)

Measured on the live library when the mask was added: 119 of the 134 stored
recommendations in worldbuilding-free genres carried a non-zero predicted
worldbuilding score.

WHAT THESE CHECKS PIN
---------------------
  1. The mask itself: fires exactly on a zero Worldbuilding weight, follows the
     EFFECTIVE (per-tenant, override-aware) weights, never mutates its input, and
     leaves the other 11 components alone.
  2. WA is byte-identical with and without it — the property that makes this a
     presentation-and-storage fix rather than a change to prediction math. If a
     future edit moves the mask before the correlation smoothing or the
     correction, this check fails.
  3. End to end through the real `correct_and_predict`, on a throwaway copy of
     books.db: a worldbuilding-free genre predicts 0.0 for all three, a
     worldbuilding genre still predicts real values, and the WA the reader is
     served is unchanged either way.

Offline: no Anthropic client is ever constructed (a cached research vector is
supplied directly), so this spends nothing and needs no key.
"""
import os
import io
import sys
import shutil
import sqlite3
import tempfile
import contextlib

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

import db_write
import db_loader
import predict_engine as pe
import research_predict as rp

CATS = ["Story", "Character", "Theme", "Aesthetics", "Worldbuilding"]
WB = list(rp.WB_COMPONENTS)
SEED = "e3160346-91f8-4334-a099-202217b376a5"

_results = []


def check(name, ok, detail=""):
    _results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def _vec(v):
    return {c: float(v) for c in db_write.FICTION_COMPONENTS}


# ---------------------------------------------------------------------------
# 1. The mask, as a pure function
# ---------------------------------------------------------------------------
def test_mask_unit():
    print("\nWORLDBUILDING MASK — the rule\n")

    realist = {c: 0.25 for c in CATS if c != "Worldbuilding"} | {"Worldbuilding": 0.0}
    fantasy = {c: 0.18 for c in CATS if c != "Worldbuilding"} | {"Worldbuilding": 0.1}
    gw = {"Literary Fiction": realist, "Epic Fantasy": fantasy}

    check("a zero Worldbuilding weight means the category does not apply",
          not rp.worldbuilding_applies("Literary Fiction", gw))
    check("a non-zero Worldbuilding weight means it does",
          rp.worldbuilding_applies("Epic Fantasy", gw))
    check("an unknown genre fails closed (no worldbuilding)",
          not rp.worldbuilding_applies("Science Fiction", gw))
    check("a None weight fails closed",
          not rp.worldbuilding_applies("X", {"X": {"Worldbuilding": None}}))
    check("a non-numeric weight fails closed rather than raising",
          not rp.worldbuilding_applies("X", {"X": {"Worldbuilding": "lots"}}))

    scores = _vec(7.5)
    masked = rp.mask_worldbuilding(scores, "Literary Fiction", gw)
    check("all three worldbuilding components are zeroed for a realist genre",
          all(masked[c] == 0.0 for c in WB),
          ", ".join(f"{c}={masked[c]}" for c in WB))
    check("the other eleven components are untouched",
          all(masked[c] == 7.5 for c in db_write.FICTION_COMPONENTS if c not in WB))
    check("the caller's dict is never mutated",
          all(scores[c] == 7.5 for c in WB))
    check("a worldbuilding genre passes through unchanged",
          rp.mask_worldbuilding(scores, "Epic Fantasy", gw) == scores)

    # The reader's own /weights override decides, not a hardcoded genre list.
    over = {"Literary Fiction": {**realist, "Worldbuilding": 0.05}}
    check("a per-tenant weight override re-enables worldbuilding for that reader",
          rp.mask_worldbuilding(scores, "Literary Fiction", over)[WB[0]] == 7.5)

    # The mask uses the 0.0 sentinel the books table stores — never None, which
    # in `recommendations` already means "saved with no prediction at all".
    check("masked components are the 0.0 sentinel, not None",
          all(masked[c] is not None and isinstance(masked[c], float) for c in WB))

    check("db_write accepts a fully masked vector (0 is inside 0-10)",
          _validates(masked))
    check("the mask names exactly the three worldbuilding components",
          set(WB) == set(db_write.WORLDBUILDING),
          f"{WB}")


def _validates(scores):
    try:
        db_write._validate_scores(scores, require_all=True)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 2. WA invariance — why this is not a change to prediction math
# ---------------------------------------------------------------------------
def test_wa_invariance():
    print("\nWORLDBUILDING MASK — WA is unchanged\n")

    src = os.path.join(PROJECT_ROOT, "books.db")
    if not os.path.exists(src):
        print("  books.db not present — skipping.")
        return
    books, gw, gcw = db_loader.load_from_db(src, user_id=SEED)

    zero = [g for g, w in gw.items() if not (w.get("Worldbuilding") or 0)]
    nonzero = [g for g, w in gw.items() if (w.get("Worldbuilding") or 0)]
    check("the library really does have worldbuilding-free genres",
          len(zero) >= 1 and len(nonzero) >= 1,
          f"{len(zero)} without, {len(nonzero)} with")

    raw = {c: 4.0 + (i % 7) * 0.7 for i, c in enumerate(db_write.FICTION_COMPONENTS)}
    worst = dict(raw) | {c: 10.0 for c in WB}      # the biggest possible WB swing

    for g in zero:
        before = rp._wa_from_components(worst, g, gw, gcw)
        after = rp._wa_from_components(rp.mask_worldbuilding(worst, g, gw), g, gw, gcw)
        if abs(before - after) > 1e-12:
            check(f"WA unchanged by the mask — {g}", False,
                  f"{before:.6f} → {after:.6f}")
            return
    check(f"WA is byte-identical with and without the mask, in all {len(zero)} "
          f"worldbuilding-free genres",
          True, "even with the three components pinned at 10.0")

    g = nonzero[0]
    check(f"a worldbuilding genre would move ({g}) — so the check above is not vacuous",
          abs(rp._wa_from_components(worst, g, gw, gcw)
              - rp._wa_from_components(dict(worst) | {c: 0.0 for c in WB},
                                       g, gw, gcw)) > 1e-6)


# ---------------------------------------------------------------------------
# 3. End to end through the real prediction path
# ---------------------------------------------------------------------------
def test_end_to_end():
    print("\nWORLDBUILDING MASK — served prediction\n")

    src = os.path.join(PROJECT_ROOT, "books.db")
    if not os.path.exists(src):
        print("  books.db not present — skipping.")
        return

    tmpd = tempfile.mkdtemp(prefix="wb_mask_")
    tmpdb = os.path.join(tmpd, "books.db")
    shutil.copy2(src, tmpdb)
    orig_cwd, orig_db = os.getcwd(), db_write.DB
    try:
        os.chdir(tmpd)
        db_write.DB = tmpdb
        db_write._backed_up_this_session = True

        con = sqlite3.connect(tmpdb)
        for t in ("books", "recommendations"):
            con.execute(f"DELETE FROM {t}")
        con.commit()
        con.close()

        _, gw0, _ = db_loader.load_from_db(tmpdb, user_id=SEED)
        realist = next(g for g, w in gw0.items() if not (w.get("Worldbuilding") or 0))
        fantasy = next(g for g, w in gw0.items() if (w.get("Worldbuilding") or 0))

        # A small rated library in each genre, plus the cached research vectors
        # the correction trains on. Realist books carry the 0.0 WB sentinel,
        # exactly as the real library does; their LLM vectors do not.
        cache = {}
        with contextlib.redirect_stdout(io.StringIO()):
            for i in range(9):
                for genre, actual in ((realist, dict(_vec(6.0 + i * 0.2))
                                       | {c: 0.0 for c in WB}),
                                      (fantasy, _vec(6.0 + i * 0.2))):
                    t = f"{genre[:4]}{i}"
                    db_write.add_book(t, genre, f"Author{i}", actual,
                                      words=120000, user_id=SEED)
                    cache[t] = {"scores": _vec(7.0 + i * 0.2), "conf": "test"}

        books, gw, gcw = db_loader.load_from_db(tmpdb, user_id=SEED)
        _c, _r2, resid_sd = pe.fit_regression(books)
        raw = _vec(8.0)

        def predict(genre, scores=None):
            return rp.correct_and_predict(
                "TargetBook", "NewAuthor", genre, dict(scores or raw), "test",
                resid_sd, books, gw, gcw, cache, corr_models=None)

        r_realist = predict(realist)
        check(f"{realist}: all three worldbuilding components come back 0.0",
              all(r_realist["scores"][c] == 0.0 for c in WB),
              ", ".join(f"{c}={r_realist['scores'][c]:.2f}" for c in WB))
        check(f"{realist}: the other eleven components are real values",
              all(r_realist["scores"][c] > 0.0
                  for c in db_write.FICTION_COMPONENTS if c not in WB))

        r_fantasy = predict(fantasy)
        check(f"{fantasy}: worldbuilding is still predicted",
              all(r_fantasy["scores"][c] > 0.0 for c in WB),
              ", ".join(f"{c}={r_fantasy['scores'][c]:.2f}" for c in WB))

        # The served WA must not have moved. Roll the UNMASKED corrected vector
        # up the same way and compare — this is the regression that matters.
        unmasked = dict(r_realist["scores"])
        for c in WB:                       # any value at all; the weight is 0
            unmasked[c] = 9.9
        check("the served WA is identical to the unmasked roll-up",
              abs(rp._wa_from_components(unmasked, realist, gw, gcw)
                  - r_realist["wa"]) < 1e-12,
              f"wa={r_realist['wa']:.6f}")

        # And the masked vector is storable — the whole point of the 0.0 sentinel.
        with contextlib.redirect_stdout(io.StringIO()):
            ok = db_write.add_recommendation(
                "TargetBook", realist, "NewAuthor", r_realist["scores"],
                user_id=SEED)
        con = sqlite3.connect(tmpdb)
        row = con.execute(
            'SELECT "Depth2","Integration","Originality" FROM recommendations '
            "WHERE title=? AND user_id=?", ("TargetBook", SEED)).fetchone()
        con.close()
        check("a masked prediction saves, and stores 0.0 in all three columns",
              ok and row is not None and all(v == 0.0 for v in row), f"{row}")
    finally:
        os.chdir(orig_cwd)
        db_write.DB = orig_db
        shutil.rmtree(tmpd, ignore_errors=True)


def main():
    print("\n=== WORLDBUILDING MASK (predict side) ===")
    test_mask_unit()
    test_wa_invariance()
    test_end_to_end()
    n, ok = len(_results), sum(_results)
    print(f"\n{ok}/{n} checks passed.")
    return 0 if ok == n else 1


if __name__ == "__main__":
    sys.exit(main())
