"""Guard: finishing a book clears its queue slot and TBR row even when the typed
title differs cosmetically from the queued one.

The regression: "Death's End" stayed at the head of the read queue after it was
rated, because POST /api/books matched the queue and the recommendation by
case-insensitive EXACT title — a dropped or curly apostrophe made both silently miss.

  * _title_key folds case, apostrophe style, a dropped apostrophe, punctuation and
    spacing, but keeps words (a subtitle is still a different title).
  * POST /api/books with "Deaths End" / "Death’s End" removes the queued
    "Death's End", marks that recommendation done=1, and leaves other entries.
  * An unrelated finish leaves the queue untouched.

Runs against a throwaway copy of books.db with the background re-predict disabled
(zero API spend).
"""
import contextlib
import io
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
    import db_backend
    from fastapi.testclient import TestClient

    src = os.path.join(PROJECT_ROOT, "books.db")
    if not os.path.exists(src):
        print("books.db not present — cannot run finish-dequeue test."); return 1
    tmpd = tempfile.mkdtemp(prefix="finish_dequeue_")
    tmpdb = os.path.join(tmpd, "books.db")
    shutil.copy2(src, tmpdb)

    import backend.main as bm

    print("\nTITLE KEY")
    k = bm._title_key
    for a, b in (("Death's End", "Deaths End"), ("Death's End", "Death’s End"),
                 ("Death's End", "  DEATH'S   END "), ("Dune: Messiah", "Dune Messiah")):
        check(f"{a!r} ~ {b!r}", k(a) == k(b), f"{k(a)!r} vs {k(b)!r}")
    check("a subtitle still differs", k("Death's End") != k("Death's End (Remembrance #3)"))
    check("different books differ", k("Dune") != k("Dune Messiah"))

    orig_cwd, orig_db, orig_repred = os.getcwd(), db_write.DB, bm._repred
    try:
        os.chdir(tmpd)
        db_write.DB = tmpdb
        db_write._backed_up_this_session = True   # never touch the real books.db
        bm._repred = None                          # no background re-predict (no API)
        bm._invalidate_engine(None)

        with TestClient(bm.app) as c:
            lib = c.get("/api/books").json()["books"]
            ref = sorted(lib, key=lambda b: b["wa"], reverse=True)[len(lib) // 2]
            scores = _flat(ref["components"])

            recs = [r["title"] for r in c.get("/api/read-queue?blurbs=0").json()["recommendations"]]
            for i, typed in enumerate(("Deaths End", "Death’s End")):
                print(f"\nFINISH — typed {typed!r}")
                queued = f"Finish Test {i}'s End"
                other = recs[0]
                # Seed via db_write directly: POST /api/recommendations enriches with
                # an LLM call, and this test must stay zero-spend.
                with contextlib.redirect_stdout(io.StringIO()):
                    ok = db_write.add_recommendation(queued, ref["genre"], "Finish Author",
                                                     scores, user_id=db_backend.DEFAULT_USER_ID)
                check("seed recommendation", ok is not False)
                c.post("/api/queue", json={"titles": [queued, other]})
                q = c.get("/api/queue").json()["titles"]
                check("seeded queue head is the test book", q[:1] == [queued], str(q[:3]))

                typed_title = typed.replace("Death", f"Finish Test {i}")
                r = c.post("/api/books", json={
                    "title": typed_title, "author": "Finish Author", "genre": ref["genre"],
                    "scores": scores, "year_read": 2026, "read_month": 1})
                check("add: 200", r.status_code == 200, r.text[:200])
                q = c.get("/api/queue").json()["titles"]
                check("finished book left the queue", queued not in q, str(q[:3]))
                check("other queue entry kept", other in q, str(q[:3]))
                recs_now = [x["title"] for x in
                            c.get("/api/read-queue?blurbs=0").json()["recommendations"]]
                check("recommendation marked done", queued not in recs_now)

            print("\nFINISH — unrelated title")
            q_before = c.get("/api/queue").json()["titles"]
            r = c.post("/api/books", json={
                "title": "Wholly Unrelated Finish", "author": "Nobody", "genre": ref["genre"],
                "scores": scores, "year_read": 2026, "read_month": 1})
            check("add: 200", r.status_code == 200, r.text[:200])
            check("queue unchanged", c.get("/api/queue").json()["titles"] == q_before)
    finally:
        os.chdir(orig_cwd)
        db_write.DB = orig_db
        bm._repred = orig_repred
        bm._invalidate_engine(None)
        shutil.rmtree(tmpd, ignore_errors=True)

    n_pass = sum(_results); n = len(_results)
    print("\n" + "=" * 60)
    print(f"  ALL {n} FINISH-DEQUEUE CHECKS PASSED" if n_pass == n else f"  {n - n_pass}/{n} FAILED")
    print("=" * 60)
    return 0 if n_pass == n else 1


if __name__ == "__main__":
    sys.exit(main())
