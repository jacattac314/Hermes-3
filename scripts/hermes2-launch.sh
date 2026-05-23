#!/bin/zsh
set -euo pipefail

ROOT="${HERMES2_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
API_URL="${HERMES2_API_URL:-http://127.0.0.1:8765}"
WEB_URL="${HERMES2_WEB_URL:-http://127.0.0.1:5173}"
LOG_DIR="$HOME/.hermes/logs/hermes2"
export PATH="$HOME/.local/bin:$HOME/bin:$HOME/.lmstudio/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
mkdir -p "$LOG_DIR"
exec >> "$LOG_DIR/desktop-launcher.log" 2>&1
echo "[$(/bin/date -Iseconds)] Hermes2 launcher start: root=$ROOT api=$API_URL web=$WEB_URL"

find_bin() {
  local name="$1"
  local candidate
  for candidate in \
    "$HOME/.local/bin/$name" \
    "/opt/homebrew/bin/$name" \
    "/usr/local/bin/$name" \
    "/usr/bin/$name" \
    "$name"; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

url_ready() {
  /usr/bin/curl -fsS --max-time 2 "$1" >/dev/null 2>&1
}

wait_for_url() {
  local url="$1"
  local label="$2"
  local attempt
  for attempt in {1..40}; do
    if url_ready "$url"; then
      return 0
    fi
    sleep 0.5
  done
  /usr/bin/osascript -e "display dialog \"Hermes 2.0 could not start $label. Check $LOG_DIR.\" buttons {\"OK\"} default button \"OK\"" >/dev/null 2>&1 || true
  return 1
}

UV_BIN="${UV_BIN:-$(find_bin uv || true)}"
NPM_BIN="${NPM_BIN:-$(find_bin npm || true)}"
HERMES2_BIN="${HERMES2_BIN:-$ROOT/.venv/bin/hermes2}"
VITE_BIN="${VITE_BIN:-$ROOT/frontend/node_modules/.bin/vite}"
STARTED_PIDS=()

if ! url_ready "$API_URL/health"; then
  if [[ ! -x "$HERMES2_BIN" && -z "$UV_BIN" ]]; then
    /usr/bin/osascript -e 'display dialog "Hermes 2.0 needs uv on PATH to start the local API." buttons {"OK"} default button "OK"' >/dev/null 2>&1 || true
    exit 1
  fi
  if [[ -x "$HERMES2_BIN" ]]; then
    echo "Starting Hermes2 API with $HERMES2_BIN"
    (cd "$ROOT" && "$HERMES2_BIN" serve --host 127.0.0.1 --port 8765 --profile default >> "$LOG_DIR/desktop-api.log" 2>&1) &
  else
    echo "Starting Hermes2 API with $UV_BIN"
    (cd "$ROOT" && "$UV_BIN" run hermes2 serve --host 127.0.0.1 --port 8765 --profile default >> "$LOG_DIR/desktop-api.log" 2>&1) &
  fi
  STARTED_PIDS+=("$!")
fi

if ! url_ready "$WEB_URL"; then
  if [[ ! -x "$VITE_BIN" && -z "$NPM_BIN" ]]; then
    /usr/bin/osascript -e 'display dialog "Hermes 2.0 needs frontend dependencies or npm on PATH to start the desktop workbench." buttons {"OK"} default button "OK"' >/dev/null 2>&1 || true
    exit 1
  fi
  if [[ -x "$VITE_BIN" && -d "$ROOT/frontend/dist" ]]; then
    echo "Starting Hermes2 workbench preview with $VITE_BIN"
    (cd "$ROOT/frontend" && "$VITE_BIN" preview --host 127.0.0.1 --port 5173 >> "$LOG_DIR/desktop-web.log" 2>&1) &
  elif [[ -x "$VITE_BIN" ]]; then
    echo "Starting Hermes2 workbench dev with $VITE_BIN"
    (cd "$ROOT/frontend" && "$VITE_BIN" --host 127.0.0.1 --port 5173 >> "$LOG_DIR/desktop-web.log" 2>&1) &
  elif [[ -d "$ROOT/frontend/dist" ]]; then
    echo "Starting Hermes2 workbench preview with $NPM_BIN"
    (cd "$ROOT/frontend" && "$NPM_BIN" run preview -- --port 5173 >> "$LOG_DIR/desktop-web.log" 2>&1) &
  else
    echo "Starting Hermes2 workbench dev with $NPM_BIN"
    (cd "$ROOT/frontend" && "$NPM_BIN" run dev -- --port 5173 >> "$LOG_DIR/desktop-web.log" 2>&1) &
  fi
  STARTED_PIDS+=("$!")
fi

wait_for_url "$API_URL/health" "API"
wait_for_url "$WEB_URL" "Workbench"

if [[ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]]; then
  /usr/bin/open -na "Google Chrome" --args \
    --app="$WEB_URL" \
    --user-data-dir="$HOME/.hermes/hermes2-chrome-profile" \
    --window-size=1180,860
else
  /usr/bin/open "$WEB_URL"
fi

if (( ${#STARTED_PIDS[@]} )); then
  echo "Waiting on service pids: ${STARTED_PIDS[*]}"
  wait "${STARTED_PIDS[@]}"
fi
