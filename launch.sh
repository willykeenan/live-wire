#!/bin/bash
# Live Wire launcher. Loopback only; no tunnel, no deploy.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
HOST="127.0.0.1"
PORT="${LIVE_WIRE_PORT:-8899}"
URL="http://${HOST}:${PORT}"
PY="${LIVE_WIRE_PYTHON:-$ROOT/.venv/bin/python}"

if [ ! -x "$PY" ]; then
  PY="$(command -v python3)"
fi

if curl -fsS --max-time 2 "$URL/api/health" >/dev/null 2>&1; then
  echo "Live Wire already running at $URL"
  if command -v open >/dev/null 2>&1; then
    open "$URL"
  fi
  exit 0
fi

# Mode flags (LIVE_WIRE_GENERATION_ENABLED, LIVE_WIRE_DEMO, LIVE_WIRE_RUMORS_ENABLED)
# are not forced here: the server defaults to the keyless demo and reads .env, and
# exporting defaults from this script would silently override a .env opt-in.
# The server is told not to open a browser because this script opens one below.
LOG="${TMPDIR:-/tmp}/livewire.log"
LIVE_WIRE_PORT="$PORT" \
LIVE_WIRE_HOST="$HOST" \
LIVE_WIRE_NO_BROWSER=1 \
nohup "$PY" "$ROOT/app/server.py" >"$LOG" 2>&1 &

ready=0
for _ in $(seq 1 40); do
  if curl -fsS --max-time 1 "$URL/api/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.25
done

if [ "$ready" -ne 1 ]; then
  echo "Live Wire failed to start. See $LOG." >&2
  exit 1
fi

if [ -z "${LIVE_WIRE_NO_BROWSER:-}" ] && command -v open >/dev/null 2>&1; then
  open "$URL"
fi
echo "Live Wire at $URL"
