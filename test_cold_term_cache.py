"""
test_cold_term_cache.py — the stale-while-revalidate cold-start-term cache.

WHY THIS EXISTS
---------------
`_fit_cold_start_term` is a leave-one-out pass over the WHOLE library (~140
`correct_and_predict` calls, ~2s on the seed). It used to be evicted by every
`_invalidate_engine()`, i.e. by every write, and refitted synchronously by whichever
read came next. On a single-process, GIL-bound backend that did not just slow the one
endpoint that needed the term — it stalled every concurrent request behind it, which is
what made a tab switch right after adding a book take seconds.

`backend/main.py` now serves the previously fitted term while a refit runs off the
request path. That is a deliberate, owner-approved trade (2026-08-20): for the couple of
seconds after a write, the served term is the one fitted on the library as it stood one
write ago. These checks pin the properties that make that trade safe:

  * a tenant with NO previous fit still gets a real one (nothing is invented), and
    concurrent first-callers share ONE fit instead of each starting their own;
  * a stale read returns immediately and serves the last good value;
  * a burst of stale reads schedules exactly one refit;
  * a refit that FAILS never clobbers the last good value;
  * a legitimate `None` fit ("too few books") is cached as None rather than refitted
    on every request forever — absence and None must stay distinguishable.

`_fit_cold_term_for` is stubbed throughout, so this never runs a real fit, never calls
an LLM, and never touches the database.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import importlib

main = importlib.import_module("backend.main")

FAILED = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


def _reset():
    with main._cold_term_cv:
        main._cold_term_cache.clear()
        main._cold_term_refitting.clear()


def run():
    uid = main._uid(None)
    real_fit = main._fit_cold_term_for
    fits = {"n": 0}

    def stub_fit(_uid):
        """Stand-in for the real ~2s fit: counts calls, returns a distinguishable value."""
        fits["n"] += 1
        time.sleep(0.4)
        return {"intercept": float(fits["n"]), "slopes": [0.1], "mu": [5.2],
                "use_series": 0, "n": 100}

    main._fit_cold_term_for = stub_fit
    try:
        print("\n" + "=" * 60)
        print("  COLD-START TERM CACHE (stale-while-revalidate)")
        print("=" * 60)

        # 1 — first-ever fit: synchronous, and single-flighted across callers.
        _reset()
        fits["n"] = 0
        got = []
        threads = [threading.Thread(target=lambda: got.append(main._fitted_cold_term(uid)))
                   for _ in range(5)]
        t0 = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.perf_counter() - t0
        check("first-ever fit is single-flighted across 5 concurrent callers",
              fits["n"] == 1, f"{fits['n']} fit(s) in {elapsed * 1000:.0f}ms")
        check("every concurrent caller receives the fitted value",
              len(got) == 5 and all(g == got[0] and g is not None for g in got))

        # 2 — a fresh read does not refit.
        before = fits["n"]
        main._fitted_cold_term(uid)
        check("a fresh cache hit does not refit", fits["n"] == before)

        # 3 — after a write the stale value is served WITHOUT blocking.
        main._invalidate_engine(None)
        t0 = time.perf_counter()
        served = main._fitted_cold_term(uid)
        elapsed = time.perf_counter() - t0
        check("a stale read returns immediately (never blocks on the refit)",
              elapsed < 0.1, f"{elapsed * 1000:.1f}ms")
        check("a stale read serves the PREVIOUS fit",
              served is not None and served["intercept"] == 1.0,
              f"intercept={served and served['intercept']}")

        # 4 — a burst of stale reads for one epoch schedules exactly one refit.
        time.sleep(1.0)                       # let step 3's refit finish
        main._invalidate_engine(None)
        before = fits["n"]
        for _ in range(10):
            main._fitted_cold_term(uid)
        time.sleep(0.1)                       # let the executor pick the refit up
        check("a burst of 10 stale reads schedules ONE refit",
              fits["n"] - before == 1, f"{fits['n'] - before} refit(s)")

        # 5 — the refit lands, and subsequent reads are fresh.
        time.sleep(1.0)
        before = fits["n"]
        served = main._fitted_cold_term(uid)
        check("the refit lands and its value is served",
              served is not None and served["intercept"] == 3.0,
              f"intercept={served and served['intercept']}")
        check("and the refreshed value is fresh (no further refit)", fits["n"] == before)

        # 6 — a failing refit must not clobber the last good value.
        def boom(_uid):
            raise RuntimeError("simulated fit failure")

        main._fit_cold_term_for = boom
        main._invalidate_engine(None)
        main._fitted_cold_term(uid)
        time.sleep(0.5)
        cached = main._cold_term_cache.get(uid)
        check("a FAILED refit keeps the last good value",
              cached is not None and cached[1] is not None and cached[1]["intercept"] == 3.0,
              f"cached={cached}")

        # 7 — a legitimate None fit is cached as None, not refitted forever.
        main._fit_cold_term_for = lambda _uid: None
        _reset()
        check("a 'too few books to fit' result is None", main._fitted_cold_term(uid) is None)
        check("and None is CACHED (absence and None stay distinguishable)",
              uid in main._cold_term_cache and main._cold_term_cache[uid][1] is None)
    finally:
        main._fit_cold_term_for = real_fit
        _reset()

    print("\n" + "=" * 60)
    if FAILED:
        print(f"  {len(FAILED)} CHECK(S) FAILED: {', '.join(FAILED)}")
    else:
        print("  ALL 11 CHECKS PASSED — the cold-term cache is healthy.")
    print("=" * 60)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(run())
