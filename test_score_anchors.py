"""
test_score_anchors.py — per-user rating-scale anchors (the prose→number map)
============================================================================
Guards the feature that lets each reader decide what number a review sentiment
is worth ("really strong / recommend it" → 8.25 by default, but yours may
differ). The anchors are applied as a monotone remap of the RAW research vector
before the engine's corrections (score_anchors), stored per tenant
(db_write.set_score_anchors), and edited in the first-run tutorial.

What this locks down:
  * DEFAULT = IDENTITY. A reader on the canonical anchors gets byte-identical
    scores and the same object back — the regression guard for every existing
    prediction, the walk-forward baseline, and test_engine's 38/38.
  * The remap is monotone and bounded: it can re-price a book, never reorder
    two components, never leave 0-10.
  * The stored write gate rejects an inverted or out-of-range scale, and a
    partial table, so a non-monotone remap can't reach the engine.
  * Tenant isolation: one reader's scale never leaks into another's.
  * PROMPT DRIFT: every band label in score_anchors.BANDS still appears verbatim
    in reresearch_and_measure.ANCHORS — the editor must describe the sentiment
    table the LLM is actually given.
  * End to end: a lowered scale actually lowers a served prediction through the
    real rp.correct_and_predict, and the default scale leaves it untouched.

Zero API spend — no LLM is involved. Runs against a throwaway copy of books.db.

Run:  python3 test_score_anchors.py     (exit 0 = pass, 1 = fail)
"""

import os
import sys
import io
import shutil
import sqlite3
import tempfile
import contextlib

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

_results = []


def check(name, condition, detail=""):
    _results.append(bool(condition))
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return bool(condition)


GENRE = "Epic Fantasy"


def _make_vec(components):
    """Per-book score vector with per-category offsets (mirrors test_engine), so
    category averages vary enough for the regression to be non-degenerate."""
    off = {c: 0.0 for c in components}
    for c in ("Plot", "Entertainment", "Action", "Ending"):            off[c] = off.get(c, 0) + 0.3
    for c in ("Depth", "Emotional Impact", "Motivations"):             off[c] = off.get(c, 0) + 0.1
    for c in ("Prose", "Narration"):                                   off[c] = off.get(c, 0) - 0.5
    for c in ("Insights", "Thought-Provokingness"):                    off[c] = off.get(c, 0) + 0.2
    for c in ("Depth2", "Integration", "Originality"):                 off[c] = off.get(c, 0) - 0.2

    def vec(base):
        return {c: min(10.0, max(0.0, float(base) + off.get(c, 0.0))) for c in components}
    return vec


def main():
    import db_backend
    import db_write
    import score_anchors as sa
    import reresearch_and_measure as rm
    import research_predict as rp
    import db_loader

    SEED = db_backend.DEFAULT_USER_ID
    USER_B = "b0000000-0000-0000-0000-0000000000bb"

    # ── 1. pure math: identity, monotonicity, bounds ─────────────────────────
    print("\nSCORE ANCHORS — the remap")
    defaults = dict(sa.DEFAULTS)
    probe = {"Plot": 9.4, "Prose": 6.2, "Ending": 3.0, "Depth": 8.6}
    check("default anchors are the identity map (same object back)",
          sa.remap_scores(probe, defaults) is probe)
    check("None anchors are the identity map (same object back)",
          sa.remap_scores(probe, None) is probe)
    check("default remap_value is the identity across the scale",
          all(abs(sa.remap_value(x / 10, defaults) - x / 10) < 1e-9
              for x in range(0, 101)),
          "0.0 … 10.0 in 0.1 steps")

    # A strict reader: every band worth ~1 point less than canonical.
    strict = {k: max(0.0, v - 1.0) for k, v in sa.DEFAULTS.items()}
    # A generous reader: every band worth ~0.5 more.
    generous = {k: min(10.0, v + 0.5) for k, v in sa.DEFAULTS.items()}

    check("a strict scale lowers every mid/high score",
          all(sa.remap_value(x, strict) < x for x in (5.5, 6.5, 7.5, 8.25, 9.25)),
          f"9.25 → {sa.remap_value(9.25, strict):.2f}")
    check("a generous scale raises them",
          all(sa.remap_value(x, generous) > x for x in (5.5, 6.5, 7.5, 8.25)),
          f"7.5 → {sa.remap_value(7.5, generous):.2f}")
    check("an anchor lands exactly on its own value",
          all(abs(sa.remap_value(b["default"], strict) - strict[b["key"]]) < 1e-9
              for b in sa.BANDS))

    xs = [i / 20 for i in range(0, 201)]
    for name, vals in (("strict", strict), ("generous", generous),
                       ("flat-top", {**sa.DEFAULTS, "favorite": 8.75, "best": 8.75}),
                       ("compressed", {k: 5.0 + (v - 5.0) * 0.25
                                       for k, v in sa.DEFAULTS.items()})):
        ys = [sa.remap_value(x, vals) for x in xs]
        check(f"remap is monotone and inside 0-10 ({name})",
              all(b >= a - 1e-9 for a, b in zip(ys, ys[1:]))
              and all(0.0 <= y <= 10.0 for y in ys),
              f"f(0)={ys[0]:.2f} f(10)={ys[-1]:.2f}")

    check("above the top anchor the scale keeps rising (no pinned ceiling)",
          sa.remap_value(9.9, strict) > sa.remap_value(9.25, strict),
          f"9.25→{sa.remap_value(9.25, strict):.2f}, 9.9→{sa.remap_value(9.9, strict):.2f}")
    check("non-numeric component values pass through untouched",
          sa.remap_scores({"Plot": None, "Prose": 8.0}, strict)["Plot"] is None)

    # ── 2. prompt drift: the editor must quote the real sentiment table ──────
    print("\nSCORE ANCHORS — prompt agreement")
    missing = [b["label"] for b in sa.BANDS if b["label"] not in rm.ANCHORS]
    check("every band label appears verbatim in reresearch_and_measure.ANCHORS",
          not missing, f"missing={missing}" if missing else f"{len(sa.BANDS)} bands")
    check("band defaults are ascending",
          all(a["default"] < b["default"] for a, b in zip(sa.BANDS, sa.BANDS[1:])))

    # ── 3. storage: round-trip, validation gate, tenant isolation ───────────
    print("\nSCORE ANCHORS — storage + write gate")
    src = os.path.join(PROJECT_ROOT, "books.db")
    if not os.path.exists(src):
        print("books.db not present — cannot run the storage/e2e checks.")
        return 1 if not all(_results) else 0
    tmpd = tempfile.mkdtemp(prefix="score_anchors_")
    tmpdb = os.path.join(tmpd, "books.db")
    shutil.copy2(src, tmpdb)

    orig_cwd = os.getcwd()
    orig_db = db_write.DB
    try:
        os.chdir(tmpd)
        db_write.DB = tmpdb
        db_write._backed_up_this_session = True
        db_write._ensure_score_anchors()
        con = sqlite3.connect(tmpdb)
        con.execute("DELETE FROM score_anchors")
        con.commit()
        con.close()

        check("a reader with no stored anchors gets the canonical defaults",
              sa.load_anchors(USER_B) == sa.DEFAULTS and not
              sa.effective_anchors(USER_B)["customized"])

        ok = db_write.set_score_anchors(strict, user_id=USER_B)
        loaded = sa.load_anchors(USER_B)
        check("set → load round-trips every band",
              ok and all(abs(loaded[k] - strict[k]) < 1e-9 for k in sa.KEYS),
              f"best={loaded['best']}")
        check("effective_anchors flags a customized scale",
              sa.effective_anchors(USER_B)["customized"]
              and len(sa.effective_anchors(USER_B)["bands"]) == len(sa.BANDS))
        check("tenant isolation: the other reader is still on defaults",
              sa.load_anchors(SEED) == sa.DEFAULTS)

        inverted = {**sa.DEFAULTS, "good": 9.9}          # "good" above "best"
        check("write gate rejects an inverted scale",
              not db_write.set_score_anchors(inverted, user_id=USER_B)
              and sa.load_anchors(USER_B)["good"] == strict["good"],
              "stored table unchanged")
        check("write gate rejects an out-of-range value",
              not db_write.set_score_anchors({**sa.DEFAULTS, "best": 11.0},
                                             user_id=USER_B))
        check("write gate rejects a partial table",
              not db_write.set_score_anchors({"best": 9.0}, user_id=USER_B))
        check("write gate rejects a non-numeric value",
              not db_write.set_score_anchors({**sa.DEFAULTS, "fine": "seven"},
                                             user_id=USER_B))
        check("equal neighbouring bands are allowed (a flat, not inverted, scale)",
              db_write.set_score_anchors({**sa.DEFAULTS, "favorite": 9.25},
                                         user_id=USER_B))

        db_write.reset_score_anchors(user_id=USER_B)
        check("reset returns the reader to the canonical defaults",
              sa.load_anchors(USER_B) == sa.DEFAULTS)

        # ── 4. end to end through the real prediction path ──────────────────
        print("\nSCORE ANCHORS — served prediction")
        con = sqlite3.connect(tmpdb)
        for t in ("books", "recommendations"):
            con.execute(f"DELETE FROM {t}")
        con.commit()
        con.close()

        FC = list(db_write.FICTION_COMPONENTS)
        vec = _make_vec(FC)
        cache = {}
        with contextlib.redirect_stdout(io.StringIO()):
            for i in range(12):
                t = f"BG{i}"
                db_write.add_book(t, GENRE, f"BGAuthor{i}", vec(6 + i % 3),
                                  words=100000, user_id=SEED)
                cache[t] = {"scores": vec(6 + i % 3), "conf": "test"}

        books, gw, gcw = db_loader.load_from_db(tmpdb, user_id=SEED)
        import predict_engine as pe
        _coeffs, _r2, resid_sd = pe.fit_regression(books)

        raw = vec(8.0)

        def predict(scores):
            return rp.correct_and_predict("TargetBook", "NewAuthor", GENRE,
                                          dict(scores), "test", resid_sd,
                                          books, gw, gcw, cache, corr_models=None)

        base = predict(raw)
        same = predict(sa.remap_scores(raw, sa.DEFAULTS))
        lower = predict(sa.remap_scores(raw, strict))
        higher = predict(sa.remap_scores(raw, generous))

        check("default anchors leave the served WA byte-identical",
              abs(base["wa"] - same["wa"]) < 1e-12,
              f"WA {base['wa']:.6f} vs {same['wa']:.6f}")
        check("a strict scale lowers the served WA",
              lower["wa"] < base["wa"] - 1e-6,
              f"{base['wa']:.3f} → {lower['wa']:.3f}")
        check("a generous scale raises it",
              higher["wa"] > base["wa"] + 1e-6,
              f"{base['wa']:.3f} → {higher['wa']:.3f}")
        check("corrected components stay inside 0-10 under a remapped scale",
              all(0.0 <= v <= 10.0 for v in higher["scores"].values()))
    finally:
        os.chdir(orig_cwd)
        db_write.DB = orig_db
        shutil.rmtree(tmpd, ignore_errors=True)

    n, total = sum(_results), len(_results)
    print("\n" + "=" * 60)
    if n == total:
        print(f"  ALL {total} CHECKS PASSED — score anchors are healthy.")
    else:
        print(f"  {total - n} of {total} CHECKS FAILED.")
    print("=" * 60)
    return 0 if n == total else 1


if __name__ == "__main__":
    sys.exit(main())
