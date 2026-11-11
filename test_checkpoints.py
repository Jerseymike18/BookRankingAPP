"""
test_checkpoints.py — mid-book reviews (reading checkpoints)
============================================================
Checkpoints are a display-only journal logged while reading. This guards:
  * validation — kind, progress 0–100 (whole), gut score 0–10, note length
  * the current-read gate — only the reader's CURRENT book (fiction: top of
    read_queue; nonfiction: status='currently-reading') takes a new checkpoint
  * tenant isolation — another user can neither read nor delete your rows
  * the engine never reads them — /api/books and /api/read-queue are unchanged
    after logging checkpoints
  * the served Currently Reading slot carries the conformal band (or none)

Runs against a throwaway copy of books.db (zero API spend).
Run:  python3 test_checkpoints.py     (exit 0 = pass, 1 = fail)
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


def quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def main():
    import db_backend
    import db_write
    from fastapi.testclient import TestClient

    SEED = db_backend.DEFAULT_USER_ID
    OTHER = "test-other-user-checkpoints"

    src = os.path.join(PROJECT_ROOT, "books.db")
    if not os.path.exists(src):
        print("books.db not present — cannot run checkpoints test."); return 1
    tmpd = tempfile.mkdtemp(prefix="checkpoints_")
    tmpdb = os.path.join(tmpd, "books.db")
    shutil.copy2(src, tmpdb)

    import backend.main as bm
    import auth

    orig_cwd = os.getcwd()
    orig_db = db_write.DB
    try:
        os.chdir(tmpd)
        db_write.DB = tmpdb
        db_write._backed_up_this_session = True    # never touch the real books.db
        db_write._checkpoints_ensured = False
        db_write._ensure_checkpoints()
        con = sqlite3.connect(tmpdb)
        con.execute("DELETE FROM reading_checkpoints")
        cur = con.execute("SELECT title FROM read_queue WHERE user_id=? ORDER BY position LIMIT 1",
                          (SEED,)).fetchone()
        second = con.execute("SELECT title FROM read_queue WHERE user_id=? ORDER BY position "
                             "LIMIT 1 OFFSET 1", (SEED,)).fetchone()
        # A nonfiction current read for the seed, so that path is exercised too.
        nf = con.execute("SELECT title FROM nonfiction_books WHERE user_id=? LIMIT 1",
                         (SEED,)).fetchone()
        if nf:
            con.execute("UPDATE nonfiction_books SET status='finished' WHERE user_id=? "
                        "AND status='currently-reading'", (SEED,))
            con.execute("UPDATE nonfiction_books SET status='currently-reading' "
                        "WHERE user_id=? AND title=?", (SEED, nf[0]))
        con.commit(); con.close()
        if not cur:
            print("seed has no read_queue — cannot run checkpoints test."); return 1
        cur = cur[0].strip()

        print("\nCHECKPOINTS — db_write validation + current-read gate")
        ok = quiet(db_write.add_checkpoint, "fiction", cur, 40, 7.25, "  good so far  ")
        check("add: valid checkpoint for the current read", ok is not None)
        check("add: gut score stored to one decimal", bool(ok) and ok["gut_score"] in (7.2, 7.3),
              str(ok and ok["gut_score"]))
        check("add: note trimmed", ok and ok["note"] == "good so far")
        check("add: title is case-insensitive, stored canonically",
              (quiet(db_write.add_checkpoint, "fiction", cur.upper(), 55, 8) or {}).get("title") == cur)
        check("add: empty note stored as NULL",
              (quiet(db_write.add_checkpoint, "fiction", cur, 60, 8, "   ") or {}).get("note", "x") is None)
        for label, args in [
            ("bad kind", ("audiobook", cur, 40, 7)),
            ("progress > 100", ("fiction", cur, 101, 7)),
            ("progress < 0", ("fiction", cur, -1, 7)),
            ("fractional progress", ("fiction", cur, 40.5, 7)),
            ("gut > 10", ("fiction", cur, 40, 10.5)),
            ("gut < 0", ("fiction", cur, 40, -0.1)),
            ("gut NaN", ("fiction", cur, 40, float("nan"))),
            ("gut non-numeric", ("fiction", cur, 40, "great")),
            ("empty title", ("fiction", "", 40, 7)),
            ("not the current read (unknown title)", ("fiction", "No Such Book Zzz", 40, 7)),
        ]:
            check(f"reject: {label}", quiet(db_write.add_checkpoint, *args) is None)
        if second:
            check("reject: queue position 2 is not the current read",
                  quiet(db_write.add_checkpoint, "fiction", second[0], 10, 7) is None)
        check("reject: note over the limit",
              quiet(db_write.add_checkpoint, "fiction", cur, 40, 7,
                    "x" * (db_write.CHECKPOINT_NOTE_MAX + 1)) is None)
        rows = db_write.get_checkpoints("fiction", cur)
        check("get: exactly the 3 valid rows, oldest first", len(rows) == 3
              and [r["progress_pct"] for r in rows] == [40, 55, 60],
              str([r["progress_pct"] for r in rows]))
        check("get: unknown kind → []", db_write.get_checkpoints("audiobook", cur) == [])
        if nf:
            check("nonfiction: current read accepts a checkpoint",
                  quiet(db_write.add_checkpoint, "nonfiction", nf[0], 30, 6.5) is not None)
            check("nonfiction: kinds are separate journals",
                  len(db_write.get_checkpoints("nonfiction", nf[0])) == 1
                  and len(db_write.get_checkpoints("fiction", cur)) == 3)

        print("\nCHECKPOINTS — tenant isolation")
        check("other user: sees none of the seed's checkpoints",
              db_write.get_checkpoints("fiction", cur, user_id=OTHER) == [])
        check("other user: cannot log on the seed's current read (no queue)",
              quiet(db_write.add_checkpoint, "fiction", cur, 40, 7, user_id=OTHER) is None)
        check("other user: cannot delete the seed's checkpoint",
              db_write.delete_checkpoint(rows[0]["id"], user_id=OTHER) is False
              and len(db_write.get_checkpoints("fiction", cur)) == 3)

        print("\nCHECKPOINTS — API + engine untouched")
        with TestClient(bm.app) as c:
            books_before = c.get("/api/books").json()
            rq_before = c.get("/api/read-queue?blurbs=0").json()
            r = c.post("/api/reading/checkpoints", json={
                "kind": "fiction", "title": cur, "progress_pct": 80, "gut_score": 9})
            check("POST: 200 for the current read", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
            new_id = r.json().get("checkpoint", {}).get("id")
            r = c.post("/api/reading/checkpoints", json={
                "kind": "fiction", "title": "No Such Book Zzz", "progress_pct": 80, "gut_score": 9})
            check("POST: 422 with a reason for a non-current book",
                  r.status_code == 422 and "current" in r.json().get("detail", ""), r.text[:120])
            r = c.post("/api/reading/checkpoints", json={
                "kind": "fiction", "title": cur, "progress_pct": 180, "gut_score": 9})
            check("POST: 422 for out-of-range progress", r.status_code == 422)
            r = c.get("/api/reading/checkpoints", params={"kind": "fiction", "title": cur})
            check("GET: lists 4 checkpoints", r.status_code == 200 and len(r.json()["checkpoints"]) == 4)
            check("engine untouched: /api/books identical after logging",
                  c.get("/api/books").json() == books_before)
            check("engine untouched: /api/read-queue identical after logging",
                  c.get("/api/read-queue?blurbs=0").json() == rq_before)

            st = c.get("/api/reading/status").json()["currently_reading"]
            if st.get("has_prediction"):
                has = "wa_low" in st and "wa_high" in st
                if bm._RESIDUALS:
                    check("status slot: carries the conformal band around WA",
                          has and st["wa_low"] <= st["wa"] <= st["wa_high"],
                          f"{st.get('wa_low')} ≤ {st['wa']} ≤ {st.get('wa_high')}")
                else:
                    check("status slot: no band invented without a residual table", not has)

            bm.app.dependency_overrides[auth.get_current_user_id] = lambda: OTHER
            try:
                check("API other user: DELETE seed's checkpoint → 404",
                      c.delete(f"/api/reading/checkpoints/{new_id}").status_code == 404)
                check("API other user: GET sees nothing",
                      c.get("/api/reading/checkpoints",
                            params={"kind": "fiction", "title": cur}).json()["checkpoints"] == [])
            finally:
                bm.app.dependency_overrides.pop(auth.get_current_user_id, None)

            print("\nCHECKPOINTS — mid-book vs final (finished books)")
            # A finished book with a Delta Log row: plant checkpoints on it
            # directly (it is no longer the current read, so add_checkpoint
            # would rightly refuse) — this is the state a finish leaves behind.
            dl = c.get("/api/delta-log").json()["entries"]
            target = dl[0] if dl else None
            r = c.get("/api/reading/checkpoints/finished", params={"kind": "fiction"})
            check("finished: the current read is NOT listed (not finished)",
                  r.status_code == 200
                  and all(b["title"].lower() != cur.lower() for b in r.json()["books"]),
                  str([b["title"] for b in r.json().get("books", [])]))
            if target:
                con = sqlite3.connect(tmpdb)
                con.executemany(
                    "INSERT INTO reading_checkpoints (id, user_id, kind, title, progress_pct, "
                    "gut_score, note, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    [("fin-a", SEED, "fiction", target["title"], 30, 6.0, None, "2026-01-01T00:00:00+00:00"),
                     ("fin-b", SEED, "fiction", target["title"], 85, 8.0, "late", "2026-01-02T00:00:00+00:00"),
                     ("fin-c", SEED, "fiction", "Never Finished Zzz", 50, 5.0, None, "2026-01-03T00:00:00+00:00")])
                con.commit(); con.close()
                books_now = {b["title"].strip().lower(): b for b in c.get("/api/books").json()["books"]}
                fb = c.get("/api/reading/checkpoints/finished", params={"kind": "fiction"}).json()["books"]
                hit = [b for b in fb if b["title"].strip().lower() == target["title"].strip().lower()]
                check("finished: the finished book is listed with both checkpoints",
                      len(hit) == 1 and [x["progress_pct"] for x in hit[0]["checkpoints"]] == [30, 85],
                      str(hit and hit[0]["checkpoints"]))
                if hit:
                    live_wa = books_now.get(target["title"].strip().lower(), {}).get("wa")
                    check("finished: final_wa is the live WA from /api/books",
                          live_wa is not None and abs(hit[0]["final_wa"] - live_wa) < 0.006,
                          f"{hit[0]['final_wa']} vs {live_wa}")
                    check("finished: predicted_wa is the Delta Log's frozen prediction",
                          hit[0]["predicted_wa"] is not None
                          and abs(hit[0]["predicted_wa"] - target["pred_wa"]) < 0.006,
                          f"{hit[0]['predicted_wa']} vs {target['pred_wa']}")
                check("finished: checkpoints on a book never finished are not listed",
                      all(b["title"] != "Never Finished Zzz" for b in fb))
                bm.app.dependency_overrides[auth.get_current_user_id] = lambda: OTHER
                try:
                    check("finished: other user sees none of it",
                          c.get("/api/reading/checkpoints/finished",
                                params={"kind": "fiction"}).json()["books"] == [])
                finally:
                    bm.app.dependency_overrides.pop(auth.get_current_user_id, None)
            if nf:
                r = c.get("/api/reading/checkpoints/finished", params={"kind": "nonfiction"})
                check("finished nonfiction: a currently-reading book is not listed",
                      r.status_code == 200 and r.json()["books"] == [], r.text[:120])

            check("API owner: DELETE own checkpoint → 200",
                  c.delete(f"/api/reading/checkpoints/{new_id}").status_code == 200)
            check("API owner: DELETE again → 404",
                  c.delete(f"/api/reading/checkpoints/{new_id}").status_code == 404)
    finally:
        os.chdir(orig_cwd)
        db_write.DB = orig_db
        db_write._checkpoints_ensured = False
        shutil.rmtree(tmpd, ignore_errors=True)

    n_pass = sum(_results); n = len(_results)
    print("\n" + "=" * 60)
    if n_pass == n:
        print(f"  ALL {n} CHECKPOINT CHECKS PASSED")
    else:
        print(f"  {n - n_pass}/{n} FAILED")
    print("=" * 60)
    return 0 if n_pass == n else 1


if __name__ == "__main__":
    raise SystemExit(main())
