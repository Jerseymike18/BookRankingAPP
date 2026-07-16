"""
test_cache_persist.py — durable research-cache regression (Brief 2, Step 2)
==========================================================================
The LLM research caches are title-keyed JSON files whose runtime writes are
EPHEMERAL on Railway (container FS, lost per deploy) → a book grounded on the live
app is re-researched (~38-110s web_search) after every deploy. Step 2 adds a
durable global `research_cache` table (db_write), consulted ONLY on a file-cache
miss and written per-entry after research. This test locks in:
  * durability — an entry written to the store is readable back (it lives in the
    DB, not any file, so it survives a file-less "fresh deploy");
  * normalization — case/whitespace title variants share one durable row;
  * wiring — research_book (base) AND WebGroundedResearcher (grounded) both HIT the
    store on a file miss and return WITHOUT an LLM call (client=None proves it);
  * no fast-path regression — load_cache stays file-only (a DB-only entry does NOT
    leak into it), so the ~50ms cache-hit path is untouched.
Hermetic: throwaway copy of books.db, no network, no LLM. Run: python3 test_cache_persist.py
"""
import os
import sys
import shutil
import tempfile
import threading

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import db_backend
import db_write
import research_layer as rl
import research_predict as rp
import compare_researchers as cr

_results = []


def check(name, cond, detail=""):
    _results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return bool(cond)


def main():
    if db_backend.backend() != "sqlite":
        print("skip: expects sqlite mode"); return 0
    src = os.path.join(PROJECT_ROOT, "books.db")
    if not os.path.exists(src):
        print("books.db not present — cannot run"); return 1
    tmpd = tempfile.mkdtemp(prefix="cache_persist_")
    tmpdb = os.path.join(tmpd, "books.db")
    shutil.copy2(src, tmpdb)

    orig_cwd, orig_db, orig_ensured = os.getcwd(), db_write.DB, db_write._research_cache_ensured
    try:
        os.chdir(tmpd)
        db_write.DB = tmpdb
        db_write._research_cache_ensured = False   # (re)create the table in the temp DB
        print("\nDURABLE RESEARCH CACHE")

        WEB = "web_grounded_cache.json"
        entry = {"scores": {c: 6.5 for c in cr.LIVE}, "conf": "grounded-test", "sources": ["x"]}

        # 1) durability — write to the store, read it straight back (it's in the DB,
        #    not any JSON file, so a fresh file-less deploy would still find it)
        db_write.put_research_cache(WEB, rl.normalize_title("Durable Test Book"), entry)
        got = db_write.get_research_cache(WEB, rl.normalize_title("Durable Test Book"))
        check("entry round-trips through the durable store", got == entry)

        # 2) normalization — put raw, get a case/whitespace variant → same row
        rp.db_cache_put(WEB, "Another Book Title", entry)
        check("case/whitespace variant hits the same durable row",
              rp.db_cache_get(WEB, "  ANOTHER   book Title ") is not None)

        # 3) NO fast-path regression — load_cache is still file-only; a DB-only entry
        #    must NOT appear in it (proves load_cache didn't start reading the DB)
        richer_file = rp.load_cache()
        check("load_cache stays file-only (durable entry does not leak in)",
              "Durable Test Book" not in richer_file
              and rl.normalize_title("Durable Test Book") not in richer_file)

        # 4) wiring — research_book (base gate) hits the store on a FILE miss and
        #    returns from_cache without an LLM call (client=None would crash otherwise)
        base = {"scores": {c: 7.0 for c in rp.LIVE}, "conf": "t", "blurb": "",
                "keywords": "", "genre": "Epic Fantasy", "words": 100000}
        rp.db_cache_put(rp.CACHE, "DB Only Base Book", base)
        r = rp.research_book("db only base book", "A", "Epic Fantasy", client=None, cache={})
        check("research_book: file miss → durable HIT (no LLM)", r[6] is True,
              f"from_cache={r[6]}")

        # 5) wiring — WebGroundedResearcher (grounded gate) hits the store on a file
        #    miss and returns without a web_search call
        web = cr.WebGroundedResearcher.__new__(cr.WebGroundedResearcher)
        web.cache, web.cache_path, web._lock = {}, WEB, threading.Lock()
        rp.db_cache_put(WEB, "DB Only Grounded Book", {"scores": {c: 6.0 for c in cr.LIVE}, "conf": "t"})
        scores, conf = web.research("DB ONLY grounded book", "A", "Epic Fantasy")
        check("WebGroundedResearcher: file miss → durable HIT (no web call)",
              len(scores) == len(cr.LIVE) and conf == "t", f"n_scores={len(scores)}")

        # 6) safety — a title in neither file nor DB is a genuine miss (no false hit)
        check("genuinely uncached title → durable miss (None)",
              rp.db_cache_get(rp.CACHE, "Totally Uncached Nonexistent Zzz 99") is None)
    finally:
        os.chdir(orig_cwd)
        db_write.DB = orig_db
        db_write._research_cache_ensured = orig_ensured
        shutil.rmtree(tmpd, ignore_errors=True)

    n_pass, n = sum(_results), len(_results)
    print("\n" + "=" * 56)
    print(f"  ALL {n} CHECKS PASSED" if n_pass == n else f"  {n - n_pass}/{n} FAILED")
    print("=" * 56)
    return 0 if n_pass == n else 1


if __name__ == "__main__":
    raise SystemExit(main())
