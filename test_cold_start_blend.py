"""
test_cold_start_blend.py — smooth cold-start shrinkage regression (Phase 4)
==========================================================================
Guards the replacement of the v1 cold-start HARD SWITCH with empirical-Bayes
shrinkage toward the seed prior.

v1: a tenant under MIN_OWN_FIT=15 books ran on 100% of the seed's fitted model;
at book 15 they snapped to 100% of their own. Two problems — their first 14
books' predictions were literally someone else's calibration, and the transition
was a discontinuity. v2 ramps the own-fit weight continuously
(``_own_fit_weight``), shrinks GENRE bias/trust on each genre's OWN count
(``_blend_ginfo``), and leaves the SEED completely unblended.

What this locks down:
  * the seed's engine tuple is a pure own fit — no blending, no drift (the
    walk-forward baseline and test_engine's 38/38 depend on this)
  * below OWN_FIT_FLOOR the tuple is byte-identical to v1's borrow
  * the ramp is continuous at the floor, monotone, and ½ at the legacy threshold
  * a genre the tenant has no books in collapses exactly to the seed's entry,
    while a genre they DO have books in moves toward their own bias
  * the engine cache is keyed on the SEED's epoch too, so a seed write can't
    leave every other tenant holding a stale borrowed model (the staleness
    window that smooth shrinkage widens from sub-threshold tenants to all of
    them)

Zero API spend — no LLM is involved on this path at all. Runs against a
throwaway copy of books.db.

Run:  python3 test_cold_start_blend.py     (exit 0 = pass, 1 = fail)
"""

import os
import sys
import io
import shutil
import sqlite3
import tempfile
import contextlib

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

_results = []


def check(name, condition, detail=""):
    _results.append(bool(condition))
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return bool(condition)


GENRE = "Epic Fantasy"
OTHER_GENRE = "Science Fiction (Hard)"   # must be a REAL genre_weights row, else add_book refuses


def _make_vec(components):
    """Per-book score vector with per-category offsets, so category averages vary
    enough for pe.fit_regression to be non-degenerate. (Mirrors test_tenant_scope.)"""
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
    import predict_engine as pe

    SEED = db_backend.DEFAULT_USER_ID
    USER_B = "b0000000-0000-0000-0000-0000000000bb"

    src = os.path.join(PROJECT_ROOT, "books.db")
    if not os.path.exists(src):
        print("books.db not present — cannot run cold-start blend test."); return 1
    tmpd = tempfile.mkdtemp(prefix="cold_blend_")
    tmpdb = os.path.join(tmpd, "books.db")
    shutil.copy2(src, tmpdb)

    # backend.main chdirs to PROJECT_ROOT at import; import BEFORE we redirect.
    import backend.main as bm

    orig_cwd = os.getcwd()
    orig_db = db_write.DB
    try:
        os.chdir(tmpd)
        db_write.DB = tmpdb
        db_write._backed_up_this_session = True
        db_write._ensure_delta_log()

        FC = list(db_write.FICTION_COMPONENTS)
        vec = _make_vec(FC)

        def wipe():
            con = sqlite3.connect(tmpdb)
            for t in ("books", "recommendations", "delta_log"):
                con.execute(f"DELETE FROM {t}")
            con.commit(); con.close()

        def add(uid, n_from, n_to, genre=GENRE, prefix="S", base_shift=0.0):
            """Add books and VERIFY they landed. db_write.add_book prints and
            declines on a rejected genre rather than raising, so a silent no-op
            would otherwise let the harness assert against an empty library."""
            with contextlib.redirect_stdout(io.StringIO()):
                for i in range(n_from, n_to):
                    db_write.add_book(f"{prefix}Book{i}", genre, f"{prefix}Author{i}",
                                      vec(6 + base_shift + i % 3), words=100000,
                                      user_id=uid)
            con = sqlite3.connect(tmpdb)
            got = con.execute(
                "SELECT COUNT(*) FROM books WHERE user_id=? AND genre=? AND title LIKE ?",
                (uid, genre, f"{prefix}Book%")).fetchone()[0]
            con.close()
            if got < (n_to - n_from):
                raise AssertionError(
                    f"harness: only {got}/{n_to - n_from} '{genre}' books landed for {uid} "
                    f"— add_book likely refused the genre")

        def clear():
            bm._engine_cache.clear()
            bm._cold_term_cache.clear()

        # ── 0. the weight function itself ────────────────────────────────────
        print("\nCOLD-START BLEND — the ramp")
        w_floor = bm._own_fit_weight(bm.OWN_FIT_FLOOR)
        check("ramp is continuous at the floor (w = 0, no jump)",
              w_floor == 0.0, f"w({bm.OWN_FIT_FLOOR}) = {w_floor}")
        check("ramp is 0 for every library at or below the floor",
              all(bm._own_fit_weight(n) == 0.0 for n in range(0, bm.OWN_FIT_FLOOR + 1)),
              f"w(0..{bm.OWN_FIT_FLOOR}) all 0")
        w_legacy = bm._own_fit_weight(bm.MIN_OWN_FIT)
        check("own-fit weight is exactly 1/2 at the legacy MIN_OWN_FIT threshold",
              abs(w_legacy - 0.5) < 1e-12, f"w({bm.MIN_OWN_FIT}) = {w_legacy}")
        ramp = [bm._own_fit_weight(n) for n in range(0, 400)]
        check("ramp is monotone non-decreasing",
              all(b >= a for a, b in zip(ramp, ramp[1:])), "no inversion over n=0..399")
        check("ramp converges toward 1 (never exceeds it)",
              max(ramp) < 1.0 and bm._own_fit_weight(2000) > 0.99,
              f"w(2000) = {bm._own_fit_weight(2000):.4f}")

        # ── 1. the seed is never blended ─────────────────────────────────────
        print("\nCOLD-START BLEND — seed invariance")
        wipe()
        add(SEED, 0, 20)
        add(SEED, 0, 6, genre=OTHER_GENRE, prefix="SF")
        clear()
        s_books, _, _, s_coeffs, s_r2, s_sd, s_ginfo, s_up = bm._get_engine(SEED)
        ref_coeffs, ref_r2, ref_sd = pe.fit_regression(s_books)
        ref_ginfo = pe.genre_bias_and_trust(s_books, ref_coeffs)
        check("seed coeffs are a PURE own fit (bit-identical to fit_regression)",
              np.array_equal(np.asarray(s_coeffs), np.asarray(ref_coeffs)),
              f"max|d| = {np.max(np.abs(np.asarray(s_coeffs) - np.asarray(ref_coeffs))):.3e}")
        check("seed resid_sd / r2 are the pure own fit's",
              s_sd == ref_sd and s_r2 == ref_r2, f"resid_sd={s_sd:.6f}")
        check("seed genre bias is the pure own fit's",
              all(s_ginfo[g]["bias"] == ref_ginfo[g]["bias"] for g in ref_ginfo),
              f"{len(ref_ginfo)} genre(s) compared")

        # ── 2. below the floor: byte-identical to the v1 borrow ──────────────
        print("\nCOLD-START BLEND — below the floor (v1 borrow preserved)")
        add(USER_B, 0, 5, prefix="B")
        clear()
        b_books, _, _, b_coeffs, b_r2, b_sd, b_ginfo, b_up = bm._get_engine(USER_B)
        check("harness: tenant B really has its own (small) library",
              len(b_books) == 5, f"B books = {len(b_books)}")
        check("below floor: coeffs are the seed's, unmodified",
              np.array_equal(np.asarray(b_coeffs), np.asarray(s_coeffs)),
              "identical array")
        check("below floor: resid_sd is the seed's, unmodified",
              b_sd == s_sd, f"{b_sd:.6f} == {s_sd:.6f}")
        check("below floor: ginfo is the seed's object, unmodified",
              b_ginfo == s_ginfo, "same genre bias/trust map")

        # ── 3. above the floor: a genuine mixture ────────────────────────────
        print("\nCOLD-START BLEND — above the floor (mixture)")
        # Give B a library with a DIFFERENT rating level so its own fit is
        # distinguishable from the seed's.
        add(USER_B, 5, 24, prefix="B", base_shift=2.0)
        clear()
        b_books, _, _, b_coeffs, _, b_sd, b_ginfo, _ = bm._get_engine(USER_B)
        n_b = len(b_books)
        w = bm._own_fit_weight(n_b)
        own_coeffs, own_r2, own_sd = pe.fit_regression(b_books)
        expect = w * np.asarray(own_coeffs) + (1 - w) * np.asarray(s_coeffs)
        check("above floor: coeffs are exactly w*own + (1-w)*seed",
              np.allclose(np.asarray(b_coeffs), expect, rtol=0, atol=1e-12),
              f"n={n_b}, w={w:.4f}")
        check("above floor: blended coeffs differ from BOTH endpoints",
              not np.allclose(np.asarray(b_coeffs), np.asarray(s_coeffs)) and
              not np.allclose(np.asarray(b_coeffs), np.asarray(own_coeffs)),
              "strictly between own and seed")

        # per-genre: B has books in GENRE, none in OTHER_GENRE (seed has both)
        check("genre with NO books for the tenant collapses to the seed entry",
              OTHER_GENRE in b_ginfo and OTHER_GENRE in s_ginfo and
              b_ginfo[OTHER_GENRE] == s_ginfo[OTHER_GENRE],
              f"{OTHER_GENRE}: bias {b_ginfo.get(OTHER_GENRE, {}).get('bias')}")
        own_ginfo = pe.genre_bias_and_trust(b_books, np.asarray(b_coeffs))
        n_g = own_ginfo[GENRE]["n"]
        wg = n_g / (n_g + bm.K_GENRE)
        expect_bias = wg * own_ginfo[GENRE]["bias"] + (1 - wg) * s_ginfo[GENRE]["bias"]
        check("genre WITH books shrinks on its OWN count, not the library count",
              abs(b_ginfo[GENRE]["bias"] - expect_bias) < 1e-12,
              f"n_genre={n_g}, w_genre={wg:.4f} (library w={w:.4f})")
        check("per-genre weight is independent of the model-level weight",
              abs(wg - w) > 1e-6, f"w_genre={wg:.4f} != w_model={w:.4f}")
        check("reported genre n stays the tenant's own (a fact, not a weight)",
              b_ginfo[GENRE]["n"] == n_g, f"n={b_ginfo[GENRE]['n']}")

        # ── 4. cache staleness: a seed write must reach every borrower ───────
        print("\nCOLD-START BLEND — seed-epoch cache keying")
        wipe()
        add(SEED, 0, 20)
        add(USER_B, 0, 4, prefix="B")          # below the floor -> rides the prior whole
        clear()
        before = np.asarray(bm._get_engine(USER_B)[3]).copy()
        seed_before = np.asarray(bm._get_engine(SEED)[3]).copy()
        check("harness: B (below floor) starts on the seed's coeffs",
              np.array_equal(before, seed_before), "matched at t0")

        add(SEED, 20, 40, base_shift=2.0)      # seed's model genuinely moves
        bm._invalidate_engine(SEED)            # ONLY the seed is invalidated
        after = np.asarray(bm._get_engine(USER_B)[3])
        seed_after = np.asarray(bm._get_engine(SEED)[3])
        check("seed's own model actually moved (harness sanity)",
              not np.allclose(seed_before, seed_after),
              f"max|d| = {np.max(np.abs(seed_after - seed_before)):.4f}")
        check("a SEED write invalidates the borrower's cached engine",
              not np.allclose(before, after),
              f"B moved by max|d| = {np.max(np.abs(after - before)):.4f}")
        check("borrower picks up the seed's NEW model, not a stale one",
              np.array_equal(after, seed_after), "B == seed after invalidation")
    finally:
        os.chdir(orig_cwd)
        db_write.DB = orig_db
        shutil.rmtree(tmpd, ignore_errors=True)

    total, passed = len(_results), sum(_results)
    print("\n" + "=" * 60)
    if passed == total:
        print(f"  ALL {total} CHECKS PASSED — cold-start shrinkage is sound.")
    else:
        print(f"  {passed}/{total} passed — {total - passed} FAILED.")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
