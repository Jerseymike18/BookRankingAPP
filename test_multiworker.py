"""
test_multiworker.py — the shared state that makes `--workers` safe.

WHY THIS EXISTS
---------------
The backend runs `--workers ${WEB_CONCURRENCY:-2}` (Procfile). Under more than one
worker process, anything kept in a module global is per PROCESS, and four pieces of
coordination state used to be exactly that:

  * the engine-cache epoch — the worst of them. A write handled by worker A left
    worker B serving the PRE-WRITE library, forever: those caches have no TTL and
    B never heard about the write. Roughly (N-1)/N of page loads would have been
    missing the book the reader had just added.
  * the background re-prediction reports the add-book panel polls for — the POST
    and the GET land on whichever worker the load balancer picks.
  * the rate-limit buckets — including the two that cap PAID Anthropic spend from
    the unauthenticated /try demo, which N workers would loosen N-fold.
  * the re-prediction write lock.

It also covers the engine cache's SINGLE-FLIGHT, which is not multi-worker state but
fails the same way if it regresses: without it every request in a page's fan-out
rebuilt the engine independently after a write, and a first attempt at it deadlocked
because `_build_engine_for` recurses into the seed's engine and a fresh tenant shares
the seed's (0, 0) epoch key.

These checks pin the shared substitutes (`db_write.app_state` / `rate_limit_hits`,
`cache_sync`, `db_write.CrossProcessLock`). They run against a THROWAWAY database and
simulate a second worker in-process, so nothing here touches books.db, the live data,
or an LLM.

NOTE ON COVERAGE: SQLite has no LISTEN/NOTIFY, so what is exercised here is the
reconciliation path — the safety net that also carries the whole mechanism if a
notification is ever missed. The NOTIFY fast path is Postgres-only and is verified by
its effect (a peer's caches drop), not by this file.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db_write
import cache_sync

FAILED = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


def run():
    tmp = tempfile.mkdtemp(prefix="ledger-mw-")
    prev_db, prev_path = db_write.DB, os.environ.get("SQLITE_PATH")
    db_write.DB = os.path.join(tmp, "t.db")
    os.environ["SQLITE_PATH"] = db_write.DB
    db_write._app_state_ensured = False
    try:
        print("\n" + "=" * 60)
        print("  MULTI-WORKER SHARED STATE")
        print("=" * 60)

        # ── 1. Epoch: the invalidation counter every worker reconciles against ──
        e1 = db_write.bump_cache_epoch("fiction", "u1")
        e2 = db_write.bump_cache_epoch("fiction", "u1")
        check("the shared epoch increments monotonically", e2 == e1 + 1, f"{e1} -> {e2}")

        def bump_many():
            for _ in range(20):
                db_write.bump_cache_epoch("fiction", "u2")

        threads = [threading.Thread(target=bump_many) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        final = int(db_write.app_state_prefix("epoch:")["epoch:fiction:u2"])
        check("concurrent bumps never lose one (in-statement increment)",
              final == 80, f"expected 80, got {final}")

        check("scopes and tenants have independent epochs",
              db_write.bump_cache_epoch("nf", "u1") == 1)

        # ── 2. A second worker reconciles a missed invalidation ────────────────
        # Simulates worker B: prime from the shared table (start-up, no
        # invalidation), then a write lands on worker A, then B's sweep runs.
        dropped = []
        cache_sync._seen.clear()
        cache_sync._prime(invalidate=False, on_invalidate=lambda s, u: dropped.append((s, u)))
        check("start-up priming does NOT invalidate (nothing is cached yet)",
              dropped == [], f"dropped={dropped}")

        db_write.bump_cache_epoch("fiction", "u1")        # a write on 'worker A'
        cache_sync._prime(invalidate=True, on_invalidate=lambda s, u: dropped.append((s, u)))
        check("a peer's write IS picked up by the reconciliation sweep",
              dropped == [("fiction", "u1")], f"dropped={dropped}")

        dropped.clear()
        cache_sync._prime(invalidate=True, on_invalidate=lambda s, u: dropped.append((s, u)))
        check("an unchanged epoch does not re-invalidate", dropped == [])

        # ── 3. A worker ignores the echo of its OWN notification ───────────────
        seen = []
        cache_sync._seen.clear()
        payload = f"{cache_sync.WORKER_ID}|fiction|u9|5"
        cache_sync._handle(payload, lambda s, u: seen.append((s, u)))
        check("a worker ignores its own NOTIFY echo", seen == [], f"seen={seen}")
        cache_sync._handle("someotherworker|fiction|u9|5", lambda s, u: seen.append((s, u)))
        check("a notification from another worker DOES invalidate",
              seen == [("fiction", "u9")], f"seen={seen}")
        seen.clear()
        cache_sync._handle("someotherworker|fiction|u9|5", lambda s, u: seen.append((s, u)))
        check("a duplicate/late notification is applied only once", seen == [])

        # ── 4. Re-prediction reports are readable from another worker ──────────
        report = {"trigger": {"title": "X"}, "affected": []}
        db_write.app_state_put("repredict:u1:tok123", json.dumps(report), ttl_s=900)
        got = db_write.app_state_get("repredict:u1:tok123")
        check("a report written by one worker is readable by another",
              got is not None and json.loads(got) == report)
        check("a report is scoped to its tenant (a stolen token reads nothing)",
              db_write.app_state_get("repredict:OTHER_USER:tok123") is None)

        # ── 5. Shared rate limits hold ACROSS workers ──────────────────────────
        allowed = [db_write.rate_limit_try("demo_live_global", "demo:global", 3, 3600.0)
                   for _ in range(5)]
        check("a shared budget caps the total, not the per-worker count",
              allowed == [True, True, True, False, False], f"{allowed}")
        check("a different principal has its own budget",
              db_write.rate_limit_try("demo_live_global", "other", 3, 3600.0) is True)
        check("an aged-out window frees the budget again",
              db_write.rate_limit_try("llm", "user:u1", 1, 0.01) is True
              and (time.sleep(0.02) or
                   db_write.rate_limit_try("llm", "user:u1", 1, 0.01) is True))
        check("Retry-After is a positive whole number of seconds",
              db_write.rate_limit_retry_after("demo_live_global", "demo:global", 3600.0) >= 1)

        # ── 6. The write lock serialises ───────────────────────────────────────
        lock = db_write.CrossProcessLock("test-repredict")
        order = []

        def hold(i):
            with lock:
                order.append(f"in{i}")
                time.sleep(0.03)
                order.append(f"out{i}")

        ts = [threading.Thread(target=hold, args=(i,)) for i in range(3)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        interleaved = any(order[i].startswith("in") and order[i + 1].startswith("in")
                          for i in range(len(order) - 1))
        check("the write lock never lets two holders overlap", not interleaved,
              " ".join(order))

        # ── 6b. Engine single-flight: concurrent misses build ONCE, and the
        #        recursive seed blend must not deadlock ─────────────────────────
        import importlib
        main = importlib.import_module("backend.main")
        builds = {"n": 0}
        real_build = main._build_engine_for

        def counting_build(u):
            builds["n"] += 1
            time.sleep(0.3)                 # widen the window concurrent callers race in
            return real_build(u)

        main._build_engine_for = counting_build
        try:
            main._engine_cache.pop(main._uid(None), None)
            main._engine_building.clear()
            got = []
            th = [threading.Thread(target=lambda: got.append(main._get_engine(None)))
                  for _ in range(4)]
            for t in th:
                t.start()
            done = []
            for t in th:
                t.join(timeout=30)
                done.append(not t.is_alive())
            check("4 concurrent engine misses trigger ONE build, not four",
                  builds["n"] == 1, f"{builds['n']} build(s)")
            check("no caller is left waiting (the seed-blend recursion cannot self-deadlock)",
                  all(done) and len(got) == 4, f"finished={sum(done)}/4, results={len(got)}")
            check("every concurrent caller gets the SAME engine object",
                  len(got) == 4 and all(g is got[0] for g in got))
        finally:
            main._build_engine_for = real_build
            main._engine_building.clear()

        # ── 6c. Read-only scope bookkeeping ───────────────────────────────────
        # The autocommit itself is a Postgres behaviour (ignored on SQLite), but the
        # SCOPE is what must never misbehave: it is thread-local because FastAPI
        # serves sync handlers on a threadpool, and it must always restore — a
        # leaked read-only flag would put a multi-statement WRITER into autocommit,
        # where a mid-way failure leaves a half-applied change.
        import db_backend as dbb
        check("readonly() is off by default", dbb.in_readonly() is False)
        with dbb.readonly():
            inside = dbb.in_readonly()
            with dbb.readonly():
                nested = dbb.in_readonly()
            still = dbb.in_readonly()
        check("readonly() turns on, nests, and restores",
              inside and nested and still and dbb.in_readonly() is False)
        try:
            with dbb.readonly():
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        check("readonly() restores even when the body raises",
              dbb.in_readonly() is False)
        seen = {}
        def worker():
            seen["other_thread"] = dbb.in_readonly()
        with dbb.readonly():
            th = threading.Thread(target=worker); th.start(); th.join()
        check("readonly() does NOT leak to another thread",
              seen.get("other_thread") is False)

        # ── 6d. The two-pooler split ──────────────────────────────────────────
        # Query traffic belongs on the TRANSACTION pooler (an idle client holds no
        # server connection, and excess demand queues instead of being refused);
        # LISTEN and session advisory locks must stay on the SESSION pooler, which
        # is the quiet failure mode — transaction mode ACCEPTS a LISTEN and then
        # never delivers.
        prev_url = os.environ.get("DATABASE_URL")
        prev_tx = os.environ.get("DB_TX_POOLER")
        try:
            os.environ["DATABASE_URL"] = (
                "postgresql://u:p@aws-1-us-east-2.pooler.supabase.com:5432/postgres")
            os.environ.pop("DB_TX_POOLER", None)
            check("query traffic is routed to the transaction pooler",
                  ":6543/" in dbb.query_dsn() and dbb.query_pooler_mode() == "transaction",
                  dbb.query_dsn().rsplit("@", 1)[1])
            check("session traffic (LISTEN, advisory locks) stays on :5432",
                  ":5432/" in dbb.session_dsn())
            os.environ["DB_TX_POOLER"] = "0"
            check("DB_TX_POOLER=0 sends everything back to the session pooler",
                  dbb.query_dsn() == dbb.session_dsn()
                  and dbb.query_pooler_mode() == "session")
            os.environ.pop("DB_TX_POOLER", None)
            os.environ["DATABASE_URL"] = "postgresql://u:p@somewhere.example:7777/db"
            check("a DSN on neither known port is left exactly as given",
                  dbb.query_dsn() == dbb.session_dsn())
        finally:
            if prev_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prev_url
            if prev_tx is None:
                os.environ.pop("DB_TX_POOLER", None)
            else:
                os.environ["DB_TX_POOLER"] = prev_tx

        # ── 7. Local dev is untouched ──────────────────────────────────────────
        check("cache_sync is inert on SQLite (one process, nothing to sync)",
              cache_sync.enabled() is False)
        cache_sync.publish("fiction", "u1")   # must be a no-op, not an error
        check("publish() is a safe no-op when sync is disabled", True)

        # ── 8. The sweeper clears expirables and keeps permanents ──────────────
        db_write.app_state_put("repredict:u1:gone", "{}", ttl_s=0.01)
        time.sleep(0.05)
        db_write.app_state_sweep()
        check("the sweeper drops expired reports",
              db_write.app_state_prefix("repredict:u1:gone") == {})
        check("the sweeper keeps the permanent epoch rows",
              "epoch:fiction:u1" in db_write.app_state_prefix("epoch:"))
    finally:
        db_write.DB = prev_db
        if prev_path is None:
            os.environ.pop("SQLITE_PATH", None)
        else:
            os.environ["SQLITE_PATH"] = prev_path
        db_write._app_state_ensured = False
        cache_sync._seen.clear()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 60)
    if FAILED:
        print(f"  {len(FAILED)} CHECK(S) FAILED: {', '.join(FAILED)}")
    else:
        print("  ALL 31 CHECKS PASSED — multi-worker shared state is healthy.")
    print("=" * 60)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(run())
