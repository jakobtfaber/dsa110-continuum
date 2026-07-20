#!/usr/bin/env bash
# Launch the DSA-110 Pipeline Console (scripts/dashboard_server.py) on port 8766.
#
# H17 usage:
#   ./scripts/run_dashboard.sh            # background, logs to state dir
#   ./scripts/run_dashboard.sh --fg       # foreground
#
# Optional automation token: $DSA110_DASH_TOKEN, else token file, else generate
# once (0600). Public host uses Cloudflare Access on /api/control (email +
# one-time code). Origin also accepts Cf-Access-Authenticated-User-Email in
# DSA110_ACCESS_EMAILS. Token remains for tests/local/automation.
set -euo pipefail

DASH_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIPELINE_REPO="${DSA110_PIPELINE_REPO:-/data/dsa110-continuum}"
[ -d "$PIPELINE_REPO" ] || PIPELINE_REPO="$DASH_REPO"
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

# Pipeline package first (photometry metrics, optional Panel mount), dash repo for local overrides.
export PYTHONPATH="$PIPELINE_REPO:$DASH_REPO${PYTHONPATH:+:$PYTHONPATH}"
export CASA_LOG_DIR="${CASA_LOG_DIR:-$STATE_DIR/casa-logs}"
export DSA110_REPO_DIR="$PIPELINE_REPO"
# H17 station coordinates (Excel export; header sniffed). Harmless if absent.
export DSA110_ANTPOS_CSV="${DSA110_ANTPOS_CSV:-/data/dsa110-antpos/antpos/data/DSA110_Station_Coordinates.csv}"

LOG="$STATE_DIR/dashboard_$(date -u +%Y%m%dT%H%M%S).log"
CMD=("$PY" "$DASH_REPO/scripts/dashboard_server.py")

echo "Console : http://$(hostname):8766/  (telescope · /pipeline · /science)"
echo "Public  : https://dsa110-continuum.jakobtfaber.com/  (pages open; /api/control via Access)"
echo "Python  : $PY"
echo "Pipeline: $PIPELINE_REPO"
echo "Token   : \$DSA110_DASH_TOKEN (automation; from ${TOKEN_FILE})"
if [ "${1:-}" = "--fg" ]; then
  exec "${CMD[@]}"
else
  nohup "${CMD[@]}" > "$LOG" 2>&1 &
  echo "PID     : $!"
  echo "Log     : $LOG"
fi
