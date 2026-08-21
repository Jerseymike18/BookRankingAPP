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
        print("  ALL 20 CHECKS PASSED — multi-worker shared state is healthy.")
    print("=" * 60)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(run())
