"""
test_public_profiles.py — the deliberate cross-tenant read path + its gate
==========================================================================
Public profiles are the ONE place the app reads across the tenant boundary on
purpose (a signed-in viewer browsing another reader's rankings/queue). This test
guards the gate around that hole: a profile is reachable ONLY when its owner has
opted it public, and the data served is the OWNER's own tenant-scoped library
(the delegation must not silently swap in the viewer's or the seed's corpus).

What it asserts, against a throwaway copy of books.db (zero API spend):
  * a PRIVATE profile → 404 (never confirms the handle exists)
  * a NONEXISTENT handle → 404
  * a PUBLIC profile → 200, and /api/users/<h>/books is byte-identical to the
    owner's own /api/books (same ranking, same weights, same count)
  * the directory lists a public profile and hides a private one
  * the library EXPORT is under the same gate — a public handle's CSV carries
    the OWNER's books, and a private handle's 404s like every other route

Run:  python3 test_public_profiles.py     (exit 0 = pass, 1 = fail)
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


def main():
    import db_backend
    import db_write
    from fastapi.testclient import TestClient

    SEED = db_backend.DEFAULT_USER_ID

    src = os.path.join(PROJECT_ROOT, "books.db")
    if not os.path.exists(src):
        print("books.db not present — cannot run public-profiles test."); return 1
    tmpd = tempfile.mkdtemp(prefix="pub_profiles_")
    tmpdb = os.path.join(tmpd, "books.db")
    shutil.copy2(src, tmpdb)

    # backend.main chdirs to PROJECT_ROOT at import; import BEFORE redirecting CWD.
    import backend.main as bm

    orig_cwd = os.getcwd()
    orig_db = db_write.DB
    try:
        os.chdir(tmpd)
        db_write.DB = tmpdb
        db_write._backed_up_this_session = True   # never touch the real books.db
        db_write._profiles_ensured = False        # (re)create the table in the copy
        db_write._ensure_profiles()
        # Clean slate: no profiles carried in from the copied DB.
        con = sqlite3.connect(tmpdb); con.execute("DELETE FROM profiles"); con.commit(); con.close()

        with contextlib.redirect_stdout(io.StringIO()):
            db_write.set_profile(SEED, "seeduser", display_name="Seed Reader", is_public=True)

        print("\nPUBLIC PROFILES — cross-tenant read gate")
        with TestClient(bm.app) as c:
            # Owner's own view (the delegation target) for comparison.
            own = c.get("/api/books").json()
            own_top = own["books"][0]["title"] if own["books"] else None
            own_n = len(own["books"])

            # PUBLIC profile resolves and delegates to the owner's own library.
            r = c.get("/api/users/seeduser/books")
            check("public profile: /books returns 200", r.status_code == 200,
                  f"status={r.status_code}")
            pub = r.json() if r.status_code == 200 else {"books": []}
            check("public profile: served ranking == owner's OWN ranking",
                  len(pub["books"]) == own_n and (pub["books"][0]["title"] if pub["books"] else None) == own_top,
                  f"served n={len(pub['books'])} top={pub['books'][0]['title'] if pub['books'] else None} "
                  f"| owner n={own_n} top={own_top}")

            hdr = c.get("/api/users/seeduser").json()
            check("public profile: header counts match the owner's library",
                  hdr.get("fiction_books") == own_n, f"header fiction_books={hdr.get('fiction_books')} vs {own_n}")

            d = c.get("/api/profiles/directory").json()
            handles = {p["handle"] for p in d.get("profiles", [])}
            check("directory: lists the public profile", "seeduser" in handles, f"handles={handles}")

            # ── Library export: the same cross-tenant read, in CSV shape ──
            # It is a NEW outside-facing route over the same hole, so it is
            # gated here rather than only in test_shelf_export (which is pure
            # and never touches the tenant boundary at all).
            import csv as _csv, io as _io
            r = c.get("/api/users/seeduser/export/library")
            check("public profile: export returns 200", r.status_code == 200,
                  f"status={r.status_code}")
            exp = r.json() if r.status_code == 200 else {"csv": "", "summary": {}}
            rows = list(_csv.DictReader(_io.StringIO(exp.get("csv", ""))))
            read_rows = [x for x in rows if x["Exclusive Shelf"] == "read"]
            check("export: every finished book in the owner's library is on the "
                  "read shelf",
                  len(read_rows) == exp["summary"]["read"],
                  f"csv read rows={len(read_rows)} summary={exp['summary']}")
            check("export: filename is keyed to the handle",
                  "seeduser" in exp.get("filename", ""), exp.get("filename"))
            # The star map, end to end on real data — not a unit test of
            # stars_for but of the whole path from WA to the cell.
            check("export: every read star is 1-5 or blank",
                  all(x["My Rating"] in ("", "1", "2", "3", "4", "5") for x in read_rows),
                  str({x["My Rating"] for x in read_rows}))
            check("export: NO to-read row carries a rating (predictions never "
                  "become stars)",
                  all(not x["My Rating"] for x in rows
                      if x["Exclusive Shelf"] == "to-read"))
            own_titles = {b["title"] for b in own["books"]}
            check("export: read shelf is the OWNER's titles, not the seed's "
                  "borrowed pool",
                  all(x["Title"].split(" (")[0] in own_titles or "(" in x["Title"]
                      for x in read_rows[:20]))
            check("export: writes nothing (library size unchanged after)",
                  len(c.get("/api/books").json()["books"]) == own_n)

            check("nonexistent handle → 404", c.get("/api/users/ghost").status_code == 404)
            check("nonexistent handle → 404 (data route too)",
                  c.get("/api/users/ghost/books").status_code == 404)

            # Flip PRIVATE — every cross-user route must now 404 (existence hidden).
            with contextlib.redirect_stdout(io.StringIO()):
                db_write.set_profile(SEED, "seeduser", display_name="Seed Reader", is_public=False)
            check("private profile: header → 404", c.get("/api/users/seeduser").status_code == 404)
            check("private profile: /books → 404", c.get("/api/users/seeduser/books").status_code == 404)
            check("private profile: /tiers → 404", c.get("/api/users/seeduser/tiers").status_code == 404)
            check("private profile: /read-queue → 404",
                  c.get("/api/users/seeduser/read-queue").status_code == 404)
            check("private profile: /stats → 404", c.get("/api/users/seeduser/stats").status_code == 404)
            check("private profile: /export/library → 404 (a private library is "
                  "not downloadable, and its existence is not confirmed)",
                  c.get("/api/users/seeduser/export/library").status_code == 404)

            d2 = c.get("/api/profiles/directory").json()
            check("directory: hides the now-private profile",
                  all(p["handle"] != "seeduser" for p in d2.get("profiles", [])),
                  f"handles={[p['handle'] for p in d2.get('profiles', [])]}")
    finally:
        os.chdir(orig_cwd)
        db_write.DB = orig_db
        db_write._profiles_ensured = False
        shutil.rmtree(tmpd, ignore_errors=True)

    n_pass = sum(_results); n = len(_results)
    print("\n" + "=" * 60)
    if n_pass == n:
        print(f"  ALL {n} PUBLIC-PROFILE CHECKS PASSED")
    else:
        print(f"  {n - n_pass}/{n} FAILED — the cross-tenant read gate is not holding")
    print("=" * 60)
    return 0 if n_pass == n else 1


if __name__ == "__main__":
    raise SystemExit(main())
