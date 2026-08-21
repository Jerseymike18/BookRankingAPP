"""
cache_sync.py — cross-process cache invalidation for the multi-worker backend.

THE PROBLEM THIS SOLVES
-----------------------
`backend/main.py` caches an expensive per-tenant engine tuple (books frame + fitted
regression + genre bias + the cold-start term) and invalidates it after a write by
bumping an in-memory epoch. Under one uvicorn process that is exactly right. Under
`--workers N` it is a silent correctness bug: worker A handles the write and bumps
ITS epoch, worker B never hears about it, and because those caches have no TTL,
worker B serves the pre-write library indefinitely. Roughly (N-1)/N of the reader's
page loads would show a library missing the book they just added.

THE MECHANISM
-------------
Postgres LISTEN/NOTIFY, with a slow reconciliation sweep as the safety net:

  * A write calls `publish(scope, user_id)`, which atomically increments a shared
    epoch row (`app_state`, key `epoch:<scope>:<user_id>`) and issues a NOTIFY.
  * Every worker holds ONE dedicated listening connection in a daemon thread.
    A notification from a DIFFERENT worker drops that tenant's caches locally,
    within milliseconds and at zero cost to the request path.
  * The same loop re-reads all epochs every RECONCILE_S and invalidates anything
    whose epoch moved without a matching notification. That covers the gaps
    NOTIFY alone leaves: a dropped connection, a redeploy, a worker that started
    after the write, or a notification lost while reconnecting.

WHY BOTH: NOTIFY alone can miss messages across a connection drop and delivers
nothing to a process that was not yet listening; polling alone would leave a
one-to-two-second window in which a worker serves the pre-write library — landing
squarely on "add a book, then open the rankings", which is the flow this whole
change set exists to make fast. Together the common path is exact and immediate,
and the failure path self-heals within one sweep.

The listening connection is deliberately NOT taken from `db_backend`'s pool: it is
held open for the process's lifetime with an active LISTEN, which is the opposite
of what a pooled, health-checked, borrow-and-return connection is for.

SQLITE / LOCAL DEV: every entry point is a no-op. Local dev is one process, so
there is nothing to synchronise and no reason to spend a write per invalidation.
Behaviour and cost there are unchanged.

Nothing here touches reader data or the scoring model. The worst case for the whole
module failing is that workers fall back to their own caches — i.e. exactly the
pre-existing single-worker behaviour — which is why every call site is best-effort.
"""

import logging
import os
import threading
import time
import uuid

import db_backend
import db_write

log = logging.getLogger(__name__)

#: Identifies this process so a worker ignores the echo of its own NOTIFY (it has
#: already invalidated locally; acting on the echo would just waste a rebuild).
WORKER_ID = uuid.uuid4().hex[:12]

#: Postgres NOTIFY channel. Payload: "<worker_id>|<scope>|<user_id>|<epoch>".
CHANNEL = "ledger_cache"

#: How long the listener blocks waiting for a notification before looping. Also the
#: granularity at which a reconciliation becomes due.
POLL_S = float(os.environ.get("CACHE_SYNC_POLL_S", "2"))
#: Full epoch re-read interval — the safety net, not the primary path.
RECONCILE_S = float(os.environ.get("CACHE_SYNC_RECONCILE_S", "10"))
#: Expired-row sweep interval (app_state TTLs + stale rate-limit hits).
SWEEP_S = float(os.environ.get("CACHE_SYNC_SWEEP_S", "300"))

_EPOCH_PREFIX = "epoch:"

_seen = {}                      # "<scope>:<user_id>" -> last epoch this worker applied
_seen_lock = threading.Lock()
_thread = None
_started = False
_start_lock = threading.Lock()


def enabled():
    """True when there is anything to synchronise: Postgres only (see module docs)."""
    return db_backend.backend() == "postgres" and \
        os.environ.get("CACHE_SYNC", "1") != "0"


def _key(scope, user_id):
    return f"{scope}:{user_id}"


def _record(scope, user_id, epoch):
    with _seen_lock:
        _seen[_key(scope, user_id)] = int(epoch)


def publish(scope, user_id):
    """Announce that `user_id`'s `scope` caches are stale.

    Best-effort by contract: the caller has ALREADY invalidated its own caches, so
    a failure here degrades to the old single-process behaviour (peers catch up on
    their next reconciliation, or at worst on restart) and must never turn a
    successful write into a failed request."""
    if not enabled():
        return
    try:
        epoch = db_write.bump_cache_epoch(scope, user_id)
        # Record it as already-applied so our own reconciliation sweep does not
        # re-invalidate caches this process just rebuilt.
        _record(scope, user_id, epoch)
        _notify(f"{WORKER_ID}|{scope}|{user_id}|{epoch}")
    except Exception:
        log.warning("cache_sync.publish failed for %s/%s", scope, user_id,
                    exc_info=True)


def _notify(payload):
    """Fire the NOTIFY on a short-lived pooled connection (the listener's own
    connection is busy waiting and must not be used to send)."""
    con = db_backend.connect(db_write.DB)
    try:
        # NOTIFY takes a literal, not a bind parameter, so the payload goes through
        # pg_notify(), which does take one — no quoting to get wrong.
        con.execute("SELECT pg_notify(?, ?)", (CHANNEL, payload))
        con.commit()
    finally:
        con.close()


def start(on_invalidate):
    """Start the listener thread once per process. `on_invalidate(scope, user_id)`
    is called for every REMOTE invalidation and must only drop local caches — it
    must not publish, or two workers would notify each other forever."""
    global _thread, _started
    if not enabled():
        return False
    with _start_lock:
        if _started:
            return True
        _started = True
        _thread = threading.Thread(target=_run, args=(on_invalidate,),
                                   name="cache-sync", daemon=True)
        _thread.start()
    return True


def _prime(invalidate=False, on_invalidate=None):
    """Read every epoch and note it as applied.

    On the FIRST pass `invalidate` is False: the process has just started, so there
    is nothing cached that could be stale, and firing invalidations would only throw
    away the startup warm. On later passes it is True, and any epoch that moved
    without a matching notification is applied now — this is the reconciliation."""
    epochs = db_write.app_state_prefix(_EPOCH_PREFIX)
    for key, raw in epochs.items():
        rest = key[len(_EPOCH_PREFIX):]
        scope, _, user_id = rest.partition(":")
        if not scope or not user_id:
            continue
        try:
            epoch = int(raw)
        except (TypeError, ValueError):
            continue
        with _seen_lock:
            prior = _seen.get(_key(scope, user_id))
            moved = prior is not None and epoch > prior
            _seen[_key(scope, user_id)] = epoch
        if invalidate and moved and on_invalidate is not None:
            log.info("cache_sync: reconciled a missed invalidation for %s/%s",
                     scope, user_id)
            _safe_invalidate(on_invalidate, scope, user_id)


def _safe_invalidate(on_invalidate, scope, user_id):
    try:
        on_invalidate(scope, user_id)
    except Exception:
        log.warning("cache_sync: local invalidation failed for %s/%s",
                    scope, user_id, exc_info=True)


def _handle(payload, on_invalidate):
    parts = (payload or "").split("|")
    if len(parts) != 4:
        return
    worker, scope, user_id, raw = parts
    if worker == WORKER_ID:
        return                      # our own echo; we invalidated before publishing
    try:
        epoch = int(raw)
    except (TypeError, ValueError):
        return
    with _seen_lock:
        prior = _seen.get(_key(scope, user_id))
        if prior is not None and epoch <= prior:
            return                  # already applied (a duplicate or a late echo)
        _seen[_key(scope, user_id)] = epoch
    _safe_invalidate(on_invalidate, scope, user_id)


def _run(on_invalidate):
    """Listener loop. Reconnects with backoff; a reconnect always reconciles, so a
    notification lost while the connection was down is picked up rather than
    leaving that worker permanently stale."""
    import select

    import psycopg2
    import psycopg2.extensions as ext

    backoff = 1.0
    first = True
    next_reconcile = 0.0
    next_sweep = time.time() + SWEEP_S
    while True:
        conn = None
        try:
            conn = psycopg2.connect(os.environ["DATABASE_URL"])
            conn.set_isolation_level(ext.ISOLATION_LEVEL_AUTOCOMMIT)
            with conn.cursor() as cur:
                cur.execute(f"LISTEN {CHANNEL}")
            # A fresh connection may have missed notifications; reconcile unless
            # this is the process's very first pass (nothing cached yet).
            _prime(invalidate=not first, on_invalidate=on_invalidate)
            first = False
            next_reconcile = time.time() + RECONCILE_S
            backoff = 1.0
            log.info("cache_sync: listening on %s as worker %s", CHANNEL, WORKER_ID)

            while True:
                select.select([conn], [], [], POLL_S)
                conn.poll()
                while conn.notifies:
                    _handle(conn.notifies.pop(0).payload, on_invalidate)
                now = time.time()
                if now >= next_reconcile:
                    _prime(invalidate=True, on_invalidate=on_invalidate)
                    next_reconcile = now + RECONCILE_S
                if now >= next_sweep:
                    try:
                        db_write.app_state_sweep()
                    except Exception:
                        log.warning("cache_sync: sweep failed", exc_info=True)
                    next_sweep = now + SWEEP_S
        except Exception:
            log.warning("cache_sync: listener connection lost; retrying in %.0fs",
                        backoff, exc_info=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
