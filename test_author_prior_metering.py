"""
test_author_prior_metering.py — the cold-start author prior is a PAID call, and it
runs on endpoints that look free.

WHY THIS EXISTS
---------------
`_build_author_prior` widens the reader's favourite authors with LLM-found analogs.
That widening is a live Anthropic call (`research_predict.find_author_analogs`), and
it is reached from three endpoints that carry no rate limit of their own:

    GET /api/read-queue        GET /api/reading/status        GET /api/engine-parameters

None of them looks like a spend path. Worse, the cache key is the reader's own
favourites tuple, so a signed-in reader who edits their favourites and reloads gets
a fresh paid call every time — unmetered, and each miss also left a permanent entry
in what used to be an uncapped module dict.

It is now behind the shared `llm` bucket, which is CLAUDE.md's "does exceeding it
cost money" rule. These checks pin the four things that are easy to undo:

  1. a cache miss consults the llm bucket at all;
  2. over budget degrades to the DIRECT-FAVOURITES prior, never an error — the same
     shape a failed analog call already produces;
  3. an un-widened prior is NOT cached, or a throttled reader would be stuck with
     the weaker nudge long after the window reopened;
  4. both prior caches are bounded.

Zero API: `find_author_analogs` is replaced by a counting stub, so nothing here
spends anything or touches books.db.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILED = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


def run():
    os.environ.setdefault("AUTH_ENABLED", "0")
    import backend.main as bm

    print("\n" + "=" * 60)
    print("  AUTHOR-PRIOR METERING (a paid call on a free-looking path)")
    print("=" * 60)

    calls = {"n": 0}

    class _StubRP:
        DISCOVER_MODEL = "stub"

        @staticmethod
        def normalize_author(a):
            return (a or "").strip().lower() or None

        @staticmethod
        def get_client(*a, **k):
            return object()

        @staticmethod
        def find_author_analogs(favs, client, **k):
            calls["n"] += 1
            return {f: [f"analog of {f}"] for f in favs}

    prev_rp, prev_enabled = bm._rp, bm._RATE_LIMIT_ENABLED
    prev_shared = bm._SHARED_BUCKETS
    bm._rp = _StubRP
    # In-process bucket only: the shared (database) path is test_multiworker's job,
    # and this file must not need a database at all.
    bm._SHARED_BUCKETS = frozenset()
    bm._RATE_LIMIT_ENABLED = True
    try:
        # ── 1. A miss makes the paid call, and a hit does not ──────────────────
        bm._author_prior_cache = bm._LRUCache(bm._TENANT_CACHE_MAX)
        bm._rl_hits.clear()
        calls["n"] = 0
        p1 = bm._build_author_prior(["Guy Gavriel Kay"], "u1")
        check("a cache miss widens with the paid analog call",
              calls["n"] == 1 and "analog of guy gavriel kay" in (p1 or {}).get("map", {}),
              f"calls={calls['n']}")
        p2 = bm._build_author_prior(["Guy Gavriel Kay"], "u1")
        check("a second identical build is served from cache (no second call)",
              calls["n"] == 1 and p2 == p1, f"calls={calls['n']}")

        # ── 2. The bucket is consulted, and it is the `llm` one ────────────────
        seen = {}
        real_rl = bm._rate_limit

        def _spy(request, bucket, *a, **k):
            seen["bucket"] = bucket
            seen["user_id"] = k.get("user_id")
            seen["raise_on_limit"] = k.get("raise_on_limit")
            return real_rl(request, bucket, *a, **k)

        bm._rate_limit = _spy
        bm._author_prior_cache = bm._LRUCache(bm._TENANT_CACHE_MAX)
        bm._rl_hits.clear()
        bm._build_author_prior(["Ursula K. Le Guin"], "u2")
        check("the paid widening is metered on the shared money bucket",
              seen.get("bucket") == "llm", f"bucket={seen.get('bucket')!r}")
        check("it is metered per reader, not globally",
              seen.get("user_id") == "u2", f"principal={seen.get('user_id')!r}")
        check("it asks GRACEFULLY — a throttled read must not 429",
              seen.get("raise_on_limit") is False)
        bm._rate_limit = real_rl

        # ── 3. Over budget degrades, never errors ─────────────────────────────
        bm._author_prior_cache = bm._LRUCache(bm._TENANT_CACHE_MAX)
        bm._rl_hits.clear()
        calls["n"] = 0
        budget = bm._local_budget("llm", bm._RL_LLM["max_calls"])
        for i in range(budget):           # spend the window
            bm._build_author_prior([f"Author {i}"], "u3")
        spent = calls["n"]
        over = bm._build_author_prior(["Someone New"], "u3")
        check("over budget makes NO further paid call",
              calls["n"] == spent, f"calls={calls['n']} (was {spent})")
        check("over budget still returns a usable prior, not an error",
              over is not None and over["map"] == {"someone new": 1.0},
              f"{over}")
        check("the degraded prior keeps the direct favourite at full weight",
              over["base"] == bm._AUTHOR_OFFSET_BASE and over["map"]["someone new"] == 1.0)

        # ── 4. The degraded prior is not cached ───────────────────────────────
        bm._rl_hits.clear()               # window reopens
        calls["n"] = 0
        again = bm._build_author_prior(["Someone New"], "u3")
        check("a throttled reader is not stuck with the weaker prior forever",
              calls["n"] == 1 and "analog of someone new" in again["map"],
              f"calls={calls['n']}")

        # ── 5. Neither prior cache can grow without bound ─────────────────────
        check("the author-prior cache is bounded",
              isinstance(bm._author_prior_cache, bm._LRUCache))
        check("the genre-prior cache is bounded",
              isinstance(bm._genre_prior_cache, bm._LRUCache))
        cap = bm._TENANT_CACHE_MAX
        bm._author_prior_cache = bm._LRUCache(cap)
        bm._rl_hits.clear()
        for i in range(cap + 25):
            bm._rl_hits.clear()           # keep every build inside budget
            bm._build_author_prior([f"Filler {i}"], "u4")
        check("it evicts rather than growing past its cap",
              len(bm._author_prior_cache) <= cap,
              f"len={len(bm._author_prior_cache)} cap={cap}")

        # ── 6. No principal at all is denied, never silently allowed ──────────
        check("a metered call with neither principal nor request is refused",
              bm._rate_limit(None, "llm", max_calls=1, window_s=60.0,
                             user_id=None, raise_on_limit=False) is False)

        # ── 7. Empty favourites still cost nothing ────────────────────────────
        calls["n"] = 0
        check("no favourites → no prior and no call",
              bm._build_author_prior([], "u5") is None
              and bm._build_author_prior(["  "], "u5") is None
              and calls["n"] == 0)
    finally:
        bm._rp = prev_rp
        bm._SHARED_BUCKETS = prev_shared
        bm._RATE_LIMIT_ENABLED = prev_enabled
        bm._rl_hits.clear()
        bm._author_prior_cache = bm._LRUCache(bm._TENANT_CACHE_MAX)

    print("\n" + "=" * 60)
    if FAILED:
        print(f"  {len(FAILED)} CHECK(S) FAILED: {', '.join(FAILED)}")
    else:
        print("  ALL 14 CHECKS PASSED — the author prior is metered.")
    print("=" * 60)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(run())
