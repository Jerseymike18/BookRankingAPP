#!/usr/bin/env bash
# start.sh — launch the full Reading Ledger stack locally.
# Run from the BookRankingAPP directory: bash start.sh
set -e

# Load nvm so `node` is on PATH
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"

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

echo ""
echo "✓ Reading Ledger is running:"
echo "  Frontend → http://localhost:$FRONTEND_PORT/rankings"
echo "  API      → http://localhost:$BACKEND_PORT/api/books"
echo ""
echo "Press Ctrl-C to stop both servers."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; echo 'Stopped.'" INT TERM
wait
