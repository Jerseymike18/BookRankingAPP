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
(cd frontend && npm run dev) &
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

cleanup() {
  kill "$BACKEND_PID" "$FRONTEND_PID" ${AUTO_PID:+"$AUTO_PID"} 2>/dev/null || true
  echo 'Stopped.'
}
trap cleanup INT TERM
wait
