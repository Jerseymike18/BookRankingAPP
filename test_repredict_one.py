"""
test_repredict_one.py — regression gate for GRANULAR (one-book) re-prediction
============================================================================
`repredict_on_add.on_book_added` is automatic and COHORT-scoped: finishing a book
sweeps every unread peer whose baseline moved. `repredict_on_add.repredict_one`
is its deliberate opposite — the reader points at ONE saved recommendation and
re-predicts just that book against the library as it stands now.

Being granular and user-triggered is exactly what makes it worth a gate: it is
the only re-prediction path a reader can aim by hand, so a scoping bug here
rewrites the wrong book, and a tagging bug pollutes the permanent accuracy
record. The checks below cover the four ways that can go wrong:

  1. SCOPE — it re-predicts THAT book and nothing else. A peer by the same author
     in the same genre (which the COHORT pass would sweep) must be untouched.
  2. TENANT ISOLATION — another tenant's identically-titled recommendation is
     never read, ranked, or written. Same discipline as test_tenant_scope.
  3. ELIGIBILITY — a finished (done=1) or absent recommendation yields None, so
     the endpoint 404s instead of resurrecting a book the reader has finished.
  4. THE DELTA-LOG TAG (the subtle one) — the audit row is tagged
     `baseline_repredict:manual:<title>`. That PREFIX is load-bearing:
     `delta_log_view` filters `baseline_repredict:*` out of the Delta Log, so
     these rows can never be mistaken for a genuine predicted-vs-actual delta
     once the reader finishes the book. A retagging that drops the prefix would
     silently corrupt the track record, and only this check would catch it.

Plus the no-op guard (an unchanged re-prediction writes nothing and logs nothing)
and a refactor guard: `ground_saved_rec` and `repredict_one` now share one
prediction core, so they must agree on the scores for the same book.

Zero API spend: the LLM research call is mocked and the web layer is disabled
(web=None), so this runs fully offline against a throwaway copy of books.db.

Run:  python3 test_repredict_one.py     (exit 0 = pass, 1 = fail)
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
    """Per-book vector with per-category offsets, so the fitted regression the
    engine builds is non-degenerate (same helper as test_tenant_scope)."""
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
    import repredict_on_add as rpa
    import delta_log_view
    import predict_engine as pe

    SEED = db_backend.DEFAULT_USER_ID
    USER_B = "b0000000-0000-0000-0000-0000000000bb"

    src = os.path.join(PROJECT_ROOT, "books.db")
    if not os.path.exists(src):
        print("books.db not present — cannot run granular-repredict test."); return 1
    tmpd = tempfile.mkdtemp(prefix="repredict_one_")
    tmpdb = os.path.join(tmpd, "books.db")
    shutil.copy2(src, tmpdb)

    # backend.main chdirs to PROJECT_ROOT at import; import BEFORE we redirect.
    import backend.main as bm
    if bm._repred is None or bm._rp is None:
        print("repredict/research unavailable — cannot run granular-repredict test."); return 1

    orig_cwd = os.getcwd()
    orig_db = db_write.DB
    orig_rl = bm._RATE_LIMIT_ENABLED
    orig_get_client = _rp.get_client
    orig_research = _rp.research_book
    orig_load_cache = _rp.load_cache
    orig_save_cache = _rp.save_cache
    try:
        os.chdir(tmpd)
        db_write.DB = tmpdb
        db_write._backed_up_this_session = True
        db_write._ensure_delta_log()

        FC = list(db_write.FICTION_COMPONENTS)
        vec = _make_vec(FC)
        _cache = {}

        # LLM mock (zero API spend). RAW is mutable: flipping it between calls is
        # how we make a re-prediction actually MOVE without touching engine math.
        RAW = {"scores": vec(7.0)}
        _rp.get_client = lambda: object()
        _rp.load_cache = lambda: _cache
        _rp.save_cache = lambda c: None
        _rp.research_book = (lambda title, author, genre, client, cache,
                             allowed_genres=None, **kw:
                             (dict(RAW["scores"]), "test", "", "", genre, 100000, True))

        con = sqlite3.connect(tmpdb)
        for t in ("books", "recommendations", "delta_log"):
            con.execute(f"DELETE FROM {t}")
        con.commit(); con.close()

        with contextlib.redirect_stdout(io.StringIO()):
            # A rated library for the correction to train on.
            for i in range(14):
                t = f"SeedBook{i}"
                db_write.add_book(t, GENRE, f"SeedAuthor{i}", vec(6 + i % 3), words=100000)
                _cache[t] = {"scores": vec(6 + i % 3), "conf": "test"}
            # TARGET and PEER share an author + genre, so the COHORT pass would
            # sweep both — the granular path must touch only TARGET.
            db_write.add_recommendation("TargetBook", GENRE, "SharedAuthor", vec(7.0), words=100000)
            db_write.add_recommendation("PeerBook", GENRE, "SharedAuthor", vec(7.0), words=100000)
            db_write.add_recommendation("DoneBook", GENRE, "SharedAuthor", vec(7.0), words=100000)
            db_write.set_done("DoneBook", True)
            # Tenant B's identically-titled rec, with a DISTINCT score vector.
            db_write.add_recommendation("TargetBook", GENRE, "SharedAuthor", vec(4.0),
                                        words=100000, user_id=USER_B)
        for t in ("TargetBook", "PeerBook", "DoneBook"):
            _cache[t] = {"scores": vec(7.0), "conf": "test"}

        def get_engine():
            return pe.build(source="db")

        def scores_of(title, uid=SEED):
            con = sqlite3.connect(tmpdb)
            cols = ", ".join(f'"{c}"' for c in FC)
            row = con.execute(
                f"SELECT {cols} FROM recommendations WHERE title=? AND user_id=?",
                (title, uid)).fetchone()
            con.close()
            return dict(zip(FC, row)) if row else None

        def delta_rows(uid=SEED):
            con = sqlite3.connect(tmpdb)
            rows = con.execute(
                "SELECT id, title, tag, pred_wa, act_wa FROM delta_log WHERE user_id=? ORDER BY id",
                (uid,)).fetchall()
            con.close()
            return rows

        class StubWeb:
            """Grounded-researcher stand-in: hands back the same vector the memory
            layer would, so `apply_grounded_overrides` is an identity. Offline and
            free — it exists because `ground_saved_rec` deliberately no-ops when
            there is no web layer at all, so comparing the two paths needs one."""
            cache: dict = {}

            def research(self, title, author, genre):
                return dict(RAW["scores"]), "test"

        def repredict(title, uid=SEED, web=None, **kw):
            with contextlib.redirect_stdout(io.StringIO()):
                return rpa.repredict_one(title, get_engine=get_engine, cache=_cache,
                                         web=web, user_id=uid, **kw)

        print("\nGRANULAR RE-PREDICTION — one book at a time")

        # ── 1. Eligibility ──────────────────────────────────────────────────
        check("absent title → None (endpoint 404s)",
              repredict("NoSuchBookXyzzy") is None)
        check("finished (done=1) rec → None (never resurrected)",
              repredict("DoneBook") is None)

        # ── 2. No-op guard: same raw vector, same library → nothing moves ────
        before_peer = scores_of("PeerBook")
        rep = repredict("TargetBook")
        check("unchanged re-prediction reports changed=False",
              rep is not None and rep["changed"] is False,
              f"changed={rep and rep['changed']}")
        check("unchanged re-prediction writes nothing",
              rep is not None and rep["written"] is False)
        check("unchanged re-prediction logs no delta row", len(delta_rows()) == 0,
              f"delta rows={len(delta_rows())}")
        check("report carries a rank out of the reader's own library",
              rep is not None and rep["total"] == 14, f"total={rep and rep['total']}")

        # ── 3. A real move: the book's raw vector changes ────────────────────
        RAW["scores"] = vec(8.5)
        _cache.pop("TargetBook", None)          # force the mocked research path
        before_target = scores_of("TargetBook")
        rep = repredict("TargetBook")
        after_target = scores_of("TargetBook")
        check("moved re-prediction reports changed=True and written=True",
              rep is not None and rep["changed"] and rep["written"],
              f"d_wa={rep and rep['d_wa']}")
        check("stored scores actually changed in place",
              after_target != before_target)
        check("report's new_wa is above the old (raw vector moved up)",
              rep is not None and rep["old_wa"] is not None
              and rep["new_wa"] > rep["old_wa"],
              f"{rep and rep['old_wa']} → {rep and rep['new_wa']}")
        check("report names the components that moved",
              rep is not None and len(rep["drivers"]) == 3
              and all(abs(d["delta"]) > 0 for d in rep["drivers"]))

        # ── 4. SCOPE — the same-author/same-genre peer is untouched ──────────
        check("GRANULAR: same-author, same-genre PEER is not re-predicted",
              scores_of("PeerBook") == before_peer,
              "the cohort pass would have swept it; this one must not")

        # ── 5. TENANT ISOLATION ─────────────────────────────────────────────
        check("another tenant's identically-titled rec is untouched",
              scores_of("TargetBook", USER_B) == vec(4.0))
        check("no delta row written under the other tenant",
              len(delta_rows(USER_B)) == 0)

        # ── 6. THE TAG (delta-log pollution guard) ──────────────────────────
        rows = delta_rows()
        check("exactly one audit row logged for the move", len(rows) == 1,
              f"rows={len(rows)}")
        tag = rows[0][2] if rows else ""
        check("audit row carries the baseline_repredict: prefix",
              tag.startswith("baseline_repredict:"), f"tag={tag!r}")
        check("audit row is identifiable as the MANUAL one-book path",
              tag == "baseline_repredict:manual:TargetBook", f"tag={tag!r}")

        # The payoff: even once the reader FINISHES this book, the manual
        # re-prediction row must not surface as a predicted-vs-actual delta.
        entries = [{"id": r[0], "title": r[1], "tag": r[2], "logged_at": "2026-01-01T00:00:00Z",
                    "pred_wa": r[3], "act_wa": r[4]} for r in rows]
        visible = delta_log_view.visible_rows(
            entries, {"targetbook"}, db_write.DELTA_BACKFILL_MARKER)
        check("delta_log_view filters the manual audit row out of the Delta Log",
              len(visible) == 0,
              "a dropped prefix here would corrupt the track record")

        # ── 7. Refactor guard: both single-book paths agree ─────────────────
        # ground_saved_rec and repredict_one now share one prediction core, so
        # for the same book and library they must produce the same new WA.
        RAW["scores"] = vec(6.2)
        _cache.pop("TargetBook", None)
        stub = StubWeb()
        rep2 = repredict("TargetBook", web=stub, dry_run=True)
        with contextlib.redirect_stdout(io.StringIO()):
            g = rpa.ground_saved_rec("TargetBook", "SharedAuthor", GENRE,
                                     get_engine=get_engine, cache=_cache,
                                     web=stub, user_id=SEED, dry_run=True)
        check("ground_saved_rec still runs after the shared-core refactor",
              g is not None, f"got {g!r}")
        check("both single-book paths agree on the new WA",
              rep2 is not None and g is not None
              and abs(rep2["new_wa"] - g["new_wa"]) < 1e-9,
              f"repredict_one={rep2 and rep2['new_wa']}, ground={g and g['new_wa']}")

        # ── 8. dry_run writes nothing ───────────────────────────────────────
        check("dry_run leaves the stored scores alone",
              scores_of("TargetBook") == after_target)
        check("dry_run logs no additional delta row", len(delta_rows()) == 1,
              f"delta rows={len(delta_rows())}")

        # ── 9. THE ENDPOINT — drive the real handler, not just the module ────
        # The handler is thin, but it is where the cold-start RANK LEAK lives: it
        # must pass the reader's OWN library as rank_pool while the correction
        # borrows the seed's. Tenant B has zero rated books, so a leak shows up
        # immediately as B being ranked out of the seed's 14.
        bm._RATE_LIMIT_ENABLED = False
        bm._engine_cache.clear(); bm._corr_statics_cache.clear()
        bm._cold_term_cache.clear()
        RAW["scores"] = vec(9.1)
        _cache.pop("TargetBook", None)
        with contextlib.redirect_stdout(io.StringIO()):
            resp = bm.repredict_recommendation("TargetBook", request=None, user_id=SEED)
        check("endpoint returns a report for the caller's own book",
              resp.get("ok") and resp["report"]["title"] == "TargetBook")
        check("endpoint's report ranks out of the caller's own library",
              resp["report"]["total"] == 14, f"total={resp['report']['total']}")

        bm._engine_cache.clear(); bm._corr_statics_cache.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            resp_b = bm.repredict_recommendation("TargetBook", request=None, user_id=USER_B)
        check("cold-start tenant is NOT ranked against the borrowed seed corpus",
              resp_b["report"]["total"] == 0,
              f"total={resp_b['report']['total']} (leak would be 14)")

        try:
            with contextlib.redirect_stdout(io.StringIO()):
                bm.repredict_recommendation("NoSuchBookXyzzy", request=None, user_id=SEED)
            check("endpoint 404s on a title not on the reader's TBR", False, "no raise")
        except Exception as exc:
            check("endpoint 404s on a title not on the reader's TBR",
                  getattr(exc, "status_code", None) == 404, f"{type(exc).__name__}")
    finally:
        os.chdir(orig_cwd)
        db_write.DB = orig_db
        bm._RATE_LIMIT_ENABLED = orig_rl
        _rp.get_client = orig_get_client
        _rp.research_book = orig_research
        _rp.load_cache = orig_load_cache
        _rp.save_cache = orig_save_cache
        shutil.rmtree(tmpd, ignore_errors=True)

    n_pass = sum(_results); n = len(_results)
    print("\n" + "=" * 60)
    if n_pass == n:
        print(f"  ALL {n} GRANULAR-REPREDICT CHECKS PASSED")
    else:
        print(f"  {n - n_pass}/{n} FAILED — one-book re-prediction is not safe")
    print("=" * 60)
    return 0 if n_pass == n else 1


if __name__ == "__main__":
    raise SystemExit(main())
