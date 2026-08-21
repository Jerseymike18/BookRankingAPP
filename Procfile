# WEB_CONCURRENCY is exported, not just read, so the value uvicorn forks on and the
# value the app sees are the SAME number. backend/main.py divides several in-process
# budgets by it (the rate-limit buckets that stay in memory, the background-grounding
# executor width); if the two disagreed, those budgets would silently be N times too
# generous. Override the count by setting WEB_CONCURRENCY in the environment.
web: WEB_CONCURRENCY=${WEB_CONCURRENCY:-2} uvicorn backend.main:app --host 0.0.0.0 --port $PORT --workers ${WEB_CONCURRENCY:-2}
