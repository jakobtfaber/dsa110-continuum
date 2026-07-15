#!/usr/bin/env bash
# Launch the DSA-110 Pipeline Console (scripts/dashboard_server.py) on port 8766.
#
# H17 usage:
#   ./scripts/run_dashboard.sh            # background, logs to state dir
#   ./scripts/run_dashboard.sh --fg       # foreground
#
# The control token is read from $DSA110_DASH_TOKEN, else from the token file,
# else generated once and persisted (0600). Without a token the server still
# runs but all mutating control routes are disabled (fail closed).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${DSA110_PYTHON:-/opt/miniforge/envs/casa6/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"

STATE_DIR="${DSA110_DASH_STATE:-/data/dsa110-continuum/state/dashboard}"
mkdir -p "$STATE_DIR" 2>/dev/null || STATE_DIR="$HOME/.dsa110-dashboard"
mkdir -p "$STATE_DIR"

TOKEN_FILE="$STATE_DIR/dash_token"
if [ -z "${DSA110_DASH_TOKEN:-}" ]; then
  if [ ! -s "$TOKEN_FILE" ]; then
    umask 177
    "$PY" -c "import secrets;print(secrets.token_urlsafe(24))" > "$TOKEN_FILE"
    umask 022
    echo "Generated control token at $TOKEN_FILE"
  fi
  export DSA110_DASH_TOKEN="$(cat "$TOKEN_FILE")"
fi

export PYTHONPATH="${PYTHONPATH:-$REPO_DIR}"
export CASA_LOG_DIR="${CASA_LOG_DIR:-$STATE_DIR/casa-logs}"
export DSA110_REPO_DIR="$REPO_DIR"

LOG="$STATE_DIR/dashboard_$(date -u +%Y%m%dT%H%M%S).log"
CMD=("$PY" "$REPO_DIR/scripts/dashboard_server.py")

echo "Console : http://$(hostname):8766/  (telescope · /pipeline · /science)"
echo "Python  : $PY"
echo "Token   : \$DSA110_DASH_TOKEN (from ${TOKEN_FILE})"
if [ "${1:-}" = "--fg" ]; then
  exec "${CMD[@]}"
else
  nohup "${CMD[@]}" > "$LOG" 2>&1 &
  echo "PID     : $!"
  echo "Log     : $LOG"
fi
