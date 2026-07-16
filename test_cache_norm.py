"""
test_cache_norm.py — research-cache key normalization regression
================================================================
Guards the latency fix (Brief 2, Step 1): the research caches are keyed by bare
book TITLE and were matched EXACTLY, so a predicted/Discover title differing only
by case or whitespace ("gardens of the moon", "Gardens of the Moon ") was a false
MISS → a needless ~6.8s (memory) / ~38–110s (web_search) LLM call for a book
already cached. `research_layer.cache_lookup` adds an exact-then-normalized lookup;
this test locks in that near-miss titles HIT while genuinely different titles do
NOT, and that both serving-path gates (research_book + WebGroundedResearcher) use
it. Hermetic: in-memory caches only, no files, no LLM/client, no network.

Run:  python3 test_cache_norm.py     (exit 0 = pass, 1 = fail)
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import research_layer as rl
import research_predict as rp
import compare_researchers as cr

_results = []


def check(name, cond, detail=""):
    _results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return bool(cond)


def main():
    print("\nRESEARCH-CACHE KEY NORMALIZATION")

    # 1) normalize_title: trim + lowercase + collapse internal whitespace
    check("normalize collapses case/whitespace",
          rl.normalize_title("  Gardens  Of  The MOON ") == "gardens of the moon")
    check("normalize handles blank/None",
          rl.normalize_title("") == "" and rl.normalize_title(None) == "")

    # 2) cache_lookup: exact + case + whitespace HIT (same entry); miss stays miss
    entry = {"scores": {"Plot": 8.0}, "conf": "x"}
    c = {"Gardens of the Moon": entry}
    check("exact hit returns the entry", rl.cache_lookup(c, "Gardens of the Moon") is entry)
    check("case-variant hits", rl.cache_lookup(c, "gardens of the moon") is entry)
    check("whitespace-variant hits", rl.cache_lookup(c, "  Gardens   of the Moon ") is entry)
    check("genuine miss → None", rl.cache_lookup(c, "Deadhouse Gates") is None)
    check("blank title → None", rl.cache_lookup(c, "") is None)

    # 3) NO false hit: two distinct titles that are NOT case/space variants
    c2 = {"The Way of Kings": {"scores": {}}}
    check("distinct title does not false-hit",
          rl.cache_lookup(c2, "The Way of Kings 2") is None)

    # 4) research_book (base gate) is wired to cache_lookup — near-miss → from_cache,
    #    client=None proves no LLM call happens (a hit returns before the client is used).
    base = {"Some Cached Book": {"scores": {x: 7.0 for x in rp.LIVE},
                                 "conf": "t", "genre": "Epic Fantasy", "words": 100000}}
    r = rp.research_book("  some cached book ", "An Author", "Epic Fantasy",
                         client=None, cache=base)
    check("research_book near-miss → from_cache=True (no LLM)", r[6] is True,
          f"from_cache={r[6]}")

    # 5) WebGroundedResearcher.research (grounded gate) is wired too — near-miss HITS
    #    its cache and returns without _search_and_score (no web_search call).
    web = cr.WebGroundedResearcher.__new__(cr.WebGroundedResearcher)  # skip __init__ (no file/client)
    web.cache = {"Some Cached Book": {"scores": {x: 6.5 for x in cr.LIVE}, "conf": "t"}}
    scores, conf = web.research("SOME CACHED BOOK", "An Author", "Epic Fantasy")
    check("WebGroundedResearcher near-miss → cache hit (no web call)",
          len(scores) == len(cr.LIVE) and conf == "t", f"n_scores={len(scores)}")

    n_pass, n = sum(_results), len(_results)
    print("\n" + "=" * 56)
    print(f"  ALL {n} CHECKS PASSED" if n_pass == n
          else f"  {n - n_pass}/{n} FAILED")
    print("=" * 56)
    return 0 if n_pass == n else 1


if __name__ == "__main__":
    raise SystemExit(main())
