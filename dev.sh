#!/usr/bin/env bash
# Runs the FastAPI backend (src/api) and the Next.js frontend together for
# local development. Ctrl-C stops both.
#
# Requires: the repo's Python venv (.venv) with requirements.txt installed,
# and Node.js 20.9+ for frontend/ (Next.js 16's minimum).
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "No .venv found. Create one first: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

if [ ! -d frontend/node_modules ]; then
  echo "frontend/node_modules missing. Run: npm --prefix frontend install" >&2
  exit 1
fi

cleanup() {
  echo "Stopping..."
  kill 0
}
trap cleanup EXIT INT TERM

.venv/bin/uvicorn src.api.main:app --reload --port 8000 &
./frontend/dev-with-node20.sh &

wait
