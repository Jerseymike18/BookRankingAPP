"""Guard: a book's standing (WA + rank) on add, and the live WA preview.

  * POST /api/books/preview (fiction) and /api/nonfiction/books/preview return the
    SAME WA the engine computes for a saved book — fed a rated book's own scores,
    the preview reproduces its library WA and rank exactly (no second roll-up).
  * Partial scores are previewed as the engine would roll them up (unscored
    components skipped), and out-of-range scores are rejected.
  * POST /api/books and /api/nonfiction/books return `standing` = the new book's
    WA and rank as the rebuilt library then reports them.

Runs against a throwaway copy of books.db with the background re-predict
disabled (zero API spend).
"""
import os
import shutil
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

_results = []


def check(label, ok, detail=""):
    _results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  — {detail}" if detail and not ok else ""))


def _flat(components):
    return {c: v for cat in components.values() for c, v in cat.items() if v is not None}


def main():
    import db_write
    from fastapi.testclient import TestClient

    src = os.path.join(PROJECT_ROOT, "books.db")
    if not os.path.exists(src):
        print("books.db not present — cannot run standing test."); return 1
    tmpd = tempfile.mkdtemp(prefix="standing_")
    tmpdb = os.path.join(tmpd, "books.db")
    shutil.copy2(src, tmpdb)

    import backend.main as bm

    orig_cwd, orig_db, orig_repred = os.getcwd(), db_write.DB, bm._repred
    try:
        os.chdir(tmpd)
        db_write.DB = tmpdb
        db_write._backed_up_this_session = True   # never touch the real books.db
        bm._repred = None                          # no background re-predict (no API)
        bm._invalidate_engine(None)
        bm._invalidate_nf_engine(None)

        with TestClient(bm.app) as c:
            for kind, base in (("fiction", "/api"), ("nonfiction", "/api/nonfiction")):
                print(f"\nSTANDING — {kind} preview")
                lib = c.get(f"{base}/books").json()["books"]
                by_wa = sorted(lib, key=lambda b: b["wa"], reverse=True)
                ref = by_wa[len(by_wa) // 2]
                scores = _flat(ref["components"])
                r = c.post(f"{base}/books/preview",
                           json={"genre": ref["genre"], "scores": scores, "title": ref["title"]})
                check("preview: 200", r.status_code == 200, r.text[:160])
                p = r.json()
                check("preview of a rated book's own scores reproduces its library WA",
                      abs(p["wa"] - ref["wa"]) < 1e-3, f"{p['wa']} vs {ref['wa']}")
                expected_rank = 1 + sum(1 for b in lib if b["title"] != ref["title"] and b["wa"] > p["wa"])
                check("preview rank = 1 + books with a higher WA", p["rank"] == expected_rank,
                      f"{p['rank']} vs {expected_rank}")
                check("preview `of` counts the library plus this book", p["of"] == len(lib),
                      f"{p['of']} vs {len(lib)}")

                # Tier: the preview must land the book exactly where the Tier List
                # (all-years cohort) has it — same label, same slot inside the tier.
                tiers = c.get(f"{base}/tiers").json()["books"]
                tscore = (lambda b: b.get("total_average") or 0) if kind == "nonfiction" else (lambda b: b["wa"])
                tref = next(b for b in tiers if b["title"] == ref["title"])
                peers = sorted([b for b in tiers if b["tier"] == tref["tier"]], key=tscore, reverse=True)
                check("preview tier = the Tier List's tier for this book", p.get("tier") == tref["tier"],
                      f"{p.get('tier')} vs {tref['tier']}")
                check("preview tier_count = that tier's size", p.get("tier_count") == len(peers),
                      f"{p.get('tier_count')} vs {len(peers)}")
                check("preview tier_index = the book's slot in the tier",
                      p.get("tier_index") == [b["title"] for b in peers].index(ref["title"]),
                      f"{p.get('tier_index')}")

                half = dict(list(scores.items())[: len(scores) // 2])
                r = c.post(f"{base}/books/preview", json={"genre": ref["genre"], "scores": half})
                check("partial preview: 200 and no higher than the full WA",
                      r.status_code == 200 and r.json()["wa"] <= p["wa"] + 1e-9, r.text[:160])
                r = c.post(f"{base}/books/preview", json={"genre": ref["genre"], "scores": {"Prose": 11}})
                check("preview rejects a score above 10", r.status_code == 422, f"{r.status_code}")

            print("\nSTANDING — fiction add returns WA + rank")
            lib = c.get("/api/books").json()["books"]
            ref = sorted(lib, key=lambda b: b["wa"], reverse=True)[3]
            body = {"title": "Standing Test Book", "author": "Test Author", "genre": ref["genre"],
                    "scores": _flat(ref["components"]), "year_read": 2026, "read_month": 1}
            r = c.post("/api/books", json=body)
            check("add: 200", r.status_code == 200, r.text[:200])
            st = r.json().get("standing")
            after = {b["title"]: b for b in c.get("/api/books").json()["books"]}
            new = after.get("Standing Test Book")
            check("add: standing present", bool(st) and new is not None, str(st))
            if st and new:
                check("standing WA = the library's WA for the new book", abs(st["wa"] - new["wa"]) < 1e-3,
                      f"{st['wa']} vs {new['wa']}")
                check("standing rank = 1 + books with a higher WA",
                      st["rank"] == 1 + sum(1 for b in after.values() if b["wa"] > new["wa"]),
                      str(st))
                check("standing `of` = library size", st["of"] == len(after), f"{st['of']} vs {len(after)}")

            print("\nSTANDING — nonfiction add returns WA + rank")
            nlib = c.get("/api/nonfiction/books").json()["books"]
            nref = nlib[0]
            r = c.post("/api/nonfiction/books", json={
                "title": "Standing Test NF", "author": "Test Author", "genre": nref["genre"],
                "scores": _flat(nref["components"]), "year_read": 2026, "read_month": 1})
            check("nf add: 200", r.status_code == 200, r.text[:200])
            st = r.json().get("standing")
            nafter = {b["title"]: b for b in c.get("/api/nonfiction/books").json()["books"]}
            new = nafter.get("Standing Test NF")
            check("nf add: standing present and matches the library WA",
                  bool(st) and new is not None and abs(st["wa"] - new["wa"]) < 1e-3, str(st))
    finally:
        os.chdir(orig_cwd)
        db_write.DB = orig_db
        bm._repred = orig_repred
        shutil.rmtree(tmpd, ignore_errors=True)

    n_pass = sum(_results); n = len(_results)
    print("\n" + "=" * 60)
    print(f"  ALL {n} STANDING CHECKS PASSED" if n_pass == n else f"  {n - n_pass}/{n} FAILED")
    print("=" * 60)
    return 0 if n_pass == n else 1


if __name__ == "__main__":
    sys.exit(main())
