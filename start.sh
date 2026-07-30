#!/usr/bin/env bash
# start.sh — launch the full Reading Ledger stack locally.
# Run from the BookRankingAPP directory: bash start.sh
set -e

# Load nvm so `node` is on PATH
export NVM_DIR="$HOME/.nvm"
# shellcheck source=/dev/null
[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"
# Activate the project virtualenv so the backend uses isolated dependencies
[ -s ".venv/bin/activate" ] && source .venv/bin/activate

BACKEND_PORT=8000
FRONTEND_PORT=3000
# Bind to loopback by default; set API_HOST=0.0.0.0 only after adding auth.
API_HOST="${API_HOST:-127.0.0.1}"

echo "▸ Starting FastAPI backend on $API_HOST:$BACKEND_PORT …"
python3 -m uvicorn backend.main:app --host "$API_HOST" --port $BACKEND_PORT --reload &
BACKEND_PID=$!

echo "▸ Starting Next.js frontend on :$FRONTEND_PORT …"
# Point the browser at the backend over IPv4 (127.0.0.1). "localhost" resolves to
# IPv6 ::1 first on macOS, where uvicorn (bound to 127.0.0.1 above) is NOT listening,
# so client-side fetches to localhost:8000 fail with "Failed to fetch". Forcing
# 127.0.0.1 here fixes writes without touching the loopback-only bind.
(cd frontend && NEXT_PUBLIC_API_URL="http://127.0.0.1:$BACKEND_PORT" npm run dev) &
FRONTEND_PID=$!

# Silent auto-publish: book edits made in the app are committed + pushed to the
# live site automatically. On by default; disable with `AUTOPUBLISH=0 bash start.sh`.
# Only starts when an `origin` remote exists (nothing to push to otherwise).
AUTO_PID=""
if [ "${AUTOPUBLISH:-1}" != "0" ] && git remote get-url origin >/dev/null 2>&1; then
  echo "● Auto-publish ON — book edits push to the live site (disable: AUTOPUBLISH=0 bash start.sh)"
  bash scripts/autopublish.sh &
  AUTO_PID=$!
fi

echo ""
echo "✓ Reading Ledger is running:"
echo "  Frontend → http://localhost:$FRONTEND_PORT/rankings"
echo "  API      → http://localhost:$BACKEND_PORT/api/books"
echo ""
echo "Press Ctrl-C to stop."

# Open the app in your default browser once the frontend is actually responding
# (Next needs a few seconds to compile first). macOS only (`open`); skips silently
# elsewhere, and opt out with `OPEN=0 bash start.sh`.
if [ "${OPEN:-1}" != "0" ] && command -v open >/dev/null 2>&1; then
  (
    for _ in $(seq 1 60); do
      if curl -sf -o /dev/null "http://127.0.0.1:$FRONTEND_PORT" 2>/dev/null; then
        open "http://localhost:$FRONTEND_PORT/"
        break
      fi
      sleep 1
    done
  ) &
fi

cleanup() {
  kill "$BACKEND_PID" "$FRONTEND_PID" ${AUTO_PID:+"$AUTO_PID"} 2>/dev/null || true
  echo 'Stopped.'
}
trap cleanup INT TERM
wait
