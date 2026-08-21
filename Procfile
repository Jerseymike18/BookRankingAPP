# Worker count is PINNED HERE, in version control — deliberately overriding whatever
# the platform sets. Railway defines WEB_CONCURRENCY itself (observed 3 on
# 2026-08-20), so honouring it meant the live worker count was set by the hosting
# environment rather than by us, and would move silently if Railway resized the box.
# Tune it by setting LEDGER_WORKERS (a name no platform sets by default), never
# WEB_CONCURRENCY.
#
# WEB_CONCURRENCY is then EXPORTED from the same value because backend/main.py and
# db_backend.py read it to divide per-process budgets — the in-memory rate-limit
# buckets, the background-grounding executor width, and DB_POOL_MAX. If the number
# uvicorn forks on and the number the app divides by ever disagreed, those budgets
# would silently be several times too generous and the Postgres connection ceiling
# would be wrong. One value, used twice, is the point.
web: WEB_CONCURRENCY=${LEDGER_WORKERS:-2} uvicorn backend.main:app --host 0.0.0.0 --port $PORT --workers ${LEDGER_WORKERS:-2}
