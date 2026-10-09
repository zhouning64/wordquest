#!/usr/bin/env bash
# Restart WordQuest: stop the copy already running on the port, then start it again in this terminal.
set -euo pipefail

usage() {
  cat <<'EOF'
Restart WordQuest: stop the copy already running on the port, then start it again in this terminal.
Usage: scripts/restart.sh              this computer only (http://127.0.0.1:8000)
       scripts/restart.sh --lan        also reachable from an iPad or phone on your Wi-Fi
       scripts/restart.sh --port 8001  use another port
EOF
}

HOST=127.0.0.1
PORT=8000
while [ $# -gt 0 ]; do
  case "$1" in
    --lan) HOST=0.0.0.0 ;;
    --port) PORT="${2:?--port needs a number}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
  shift
done

cd "$(dirname "$0")/.."
ROOT=$(pwd -P)
if [ ! -x .venv/bin/python ]; then
  echo "No .venv/bin/python in $ROOT; set up the project first (see README)." >&2
  exit 1
fi

# A process belongs to this WordQuest when it (or its parent, for --reload/--workers children)
# runs "uvicorn ... app.main:app" from this project folder.
is_ours() {
  local pid=$1 cmd cwd
  cmd=$(ps -o command= -p "$pid" 2>/dev/null || true)
  [[ "$cmd" == *uvicorn*app.main:app* ]] || return 1
  cwd=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)
  [ "$cwd" = "$ROOT" ]
}

pids=$(lsof -nP -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | sort -u || true)
to_stop=""
for pid in $pids; do
  kill -0 "$pid" 2>/dev/null || continue
  if is_ours "$pid"; then
    to_stop="$to_stop $pid"
  else
    parent=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ' || true)
    if [ -n "$parent" ] && is_ours "$parent"; then
      to_stop="$to_stop $parent"
    else
      echo "Port $PORT is used by another program ($(ps -o command= -p "$pid" 2>/dev/null || echo "process $pid"))." >&2
      echo "Stop that program first, or pick another port with --port." >&2
      exit 1
    fi
  fi
done

for pid in $(echo $to_stop | tr ' ' '\n' | sort -u); do
  kill -0 "$pid" 2>/dev/null || continue
  echo "Stopping the running WordQuest (process $pid)..."
  kill -TERM "$pid" 2>/dev/null || true
  for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
  if kill -0 "$pid" 2>/dev/null; then
    echo "It did not stop within 10 seconds; forcing it."
    kill -KILL "$pid" 2>/dev/null || true
    sleep 1
  fi
done

echo "Starting WordQuest on http://$HOST:$PORT (press Ctrl+C to stop)"
exec .venv/bin/python -m uvicorn app.main:app --host "$HOST" --port "$PORT"
