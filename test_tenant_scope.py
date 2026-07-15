"""
test_tenant_scope.py — multi-tenant scope regression for the prediction path
============================================================================
Guards the fix for the cold-start rank leak (Brief 1): a fresh account with zero
rated books used to get "predicted rank #2 of 129", where 129 was the SEED
tenant's corpus size. The fiction research-predict endpoint ranked a prediction
against the *correction pool*, which for a below-threshold (cold-start) tenant is
the seed's calibrated books borrowed in — so a new user saw the seed's library as
their denominator, grounding counts, and rank.

WHY THE CORPUS-HOLDER IS THE SEED (the subtle bit)
--------------------------------------------------
A cold-start tenant only ever borrows the SEED (db_backend.DEFAULT_USER_ID), never
some arbitrary other tenant. So the faithful reproduction gives the corpus to the
SEED and leaves tenant B empty; the leak is B's reported total/grounding tracking
the SEED's corpus. Assert B's response reflects B's OWN (empty) library, and stays
invariant as the seed's corpus GROWS. On current main this fails (B.total follows
the seed: 12 → 16); with the fix it passes (B.total stays 0).

This drives the REAL endpoint (backend.main.predict_research) — not
correct_and_predict directly — so it also catches the endpoint dropping the
rank_pool= argument. The LLM call is mocked (zero API spend); everything else is
the live code path against a throwaway two-tenant copy of books.db.

Run:  python3 test_tenant_scope.py     (exit 0 = pass, 1 = fail)
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
    """A per-book score vector with small per-category offsets, so the seed's
    category averages vary enough for pe.fit_regression to be non-degenerate."""
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
    import research_predict as _rp

    SEED = db_backend.DEFAULT_USER_ID
    USER_B = "b0000000-0000-0000-0000-0000000000bb"   # a fresh tenant, zero books

    # --- throwaway two-tenant DB (copy of the real one) ----------------------
    src = os.path.join(PROJECT_ROOT, "books.db")
    if not os.path.exists(src):
        print("books.db not present — cannot run tenant-scope test."); return 1
    tmpd = tempfile.mkdtemp(prefix="tenant_scope_")
    tmpdb = os.path.join(tmpd, "books.db")
    shutil.copy2(src, tmpdb)

    # backend.main chdirs to PROJECT_ROOT at import; import BEFORE we redirect.
    import backend.main as bm
    if bm._rp is None:
        print("research_predict unavailable — cannot run tenant-scope test."); return 1

    orig_cwd = os.getcwd()
    orig_db = db_write.DB
    orig_cold = bm.COLD_START_TERM_ENABLED
    orig_get_client = _rp.get_client
    orig_research = _rp.research_book
    orig_load_cache = _rp.load_cache
    orig_save_cache = _rp.save_cache
    try:
        # Every module defaults to a RELATIVE "books.db", so CWD is the lever the
        # app itself uses; point it (and db_write's absolute handle) at the copy.
        os.chdir(tmpd)
        db_write.DB = tmpdb
        db_write._backed_up_this_session = True
        db_write._ensure_delta_log()

        FC = list(db_write.FICTION_COMPONENTS)
        vec = _make_vec(FC)
        _cache = {}

        # LLM mock: research returns a fixed raw-score vector (zero API spend).
        SCORES = vec(7.0)
        _rp.get_client = lambda: object()
        _rp.load_cache = lambda: _cache
        _rp.save_cache = lambda c: None
        _rp.research_book = (lambda title, author, genre, client, cache,
                             allowed_genres=None, **kw:
                             (dict(SCORES), "test", "", "", genre, 100000, True))
        bm.COLD_START_TERM_ENABLED = False   # isolate the scope fix from the cold term

        def wipe():
            con = sqlite3.connect(tmpdb)
            for t in ("books", "recommendations", "delta_log"):
                con.execute(f"DELETE FROM {t}")
            con.commit(); con.close()

        def seed_add(n_from, n_to):
            """Add seed books [n_from, n_to) — all Epic Fantasy, distinct authors."""
            with contextlib.redirect_stdout(io.StringIO()):
                for i in range(n_from, n_to):
                    t = f"SeedBook{i}"
                    db_write.add_book(t, GENRE, f"SeedAuthor{i}", vec(6 + i % 3), words=100000)
                    _cache[t] = {"scores": vec(6 + i % 3), "conf": "test"}

        def clear_caches():
            bm._engine_cache.clear(); bm._nf_engine_cache.clear()
            bm._cold_term_cache.clear(); bm._author_prior_cache.clear()

        def predict_as(uid, title="ZZScopeProbe", author="NobodyAuthor"):
            clear_caches()
            req = bm.ResearchRequest(title=title, author=author, genre=GENRE)
            return bm.predict_research(req, user_id=uid, user_md={})

        # ── Scenario ────────────────────────────────────────────────────────
        wipe()
        seed_add(0, 12)                       # SEED (the borrow source) = 12 books
        print("\nTENANT SCOPE — fiction research prediction")

        # Harness sanity: the seed corpus is real and detectable (guards against a
        # misrouted DB path silently making every library look empty).
        seed_own = len(bm._get_engine(SEED)[0])
        check("harness: seed corpus is populated (not misrouted)", seed_own == 12,
              f"seed _get_engine books = {seed_own}")

        b1 = predict_as(USER_B)
        check("B (0 books): total is B's own library, not the seed's",
              b1["total"] == 0, f"total={b1['total']} (leak would be 12)")
        check("B (0 books): grounding counts are B's own (0 / 0)",
              b1["n_author"] == 0 and b1["n_genre"] == 0,
              f"n_author={b1['n_author']}, n_genre={b1['n_genre']}")
        check("B (0 books): rank is #1 in an empty library",
              b1["rank"] == 1, f"rank={b1['rank']}")

        seed_ok = predict_as(SEED)
        check("SEED (12 books): still ranks against its OWN full corpus",
              seed_ok["total"] == 12, f"total={seed_ok['total']}")

        seed_add(12, 16)                      # grow the seed corpus 12 → 16
        b2 = predict_as(USER_B)
        check("B stays scoped as the seed corpus GROWS 12→16 (no leak)",
              b2["total"] == 0 and b2["n_genre"] == 0,
              f"total={b2['total']}, n_genre={b2['n_genre']} (leak would be 16)")
        check("invariance: B's total does not track the seed corpus",
              b1["total"] == b2["total"] == 0,
              f"B.total {b1['total']} then {b2['total']}; seed grew 12→16")
    finally:
        os.chdir(orig_cwd)
        db_write.DB = orig_db
        bm.COLD_START_TERM_ENABLED = orig_cold
        _rp.get_client = orig_get_client
        _rp.research_book = orig_research
        _rp.load_cache = orig_load_cache
        _rp.save_cache = orig_save_cache
        shutil.rmtree(tmpd, ignore_errors=True)

    n_pass = sum(_results); n = len(_results)
    print("\n" + "=" * 60)
    if n_pass == n:
        print(f"  ALL {n} TENANT-SCOPE CHECKS PASSED")
    else:
        print(f"  {n - n_pass}/{n} FAILED — a prediction-path read leaks another tenant's corpus")
    print("=" * 60)
    return 0 if n_pass == n else 1


if __name__ == "__main__":
    raise SystemExit(main())
