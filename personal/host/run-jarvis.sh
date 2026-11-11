#!/usr/bin/env bash
# Jarvis supervisor wrapper. systemd (Linux) or launchd (macOS) runs this
# at boot and restarts it whenever it exits.
#
# - If JARVIS_CMD is set in ~/.jarvis/host.env, it runs that command
#   (Phase 1 sets it to the OpenJarvis server: `jarvis serve`).
# - Otherwise it runs in "placeholder" mode so you can prove auto-start works
#   before anything else is installed.
# In both modes it writes a heartbeat file every 30s for healthcheck.sh.
set -uo pipefail

JARVIS_HOME="${JARVIS_HOME:-$HOME/.jarvis}"
LOG_DIR="$JARVIS_HOME/logs"
HEARTBEAT="$JARVIS_HOME/heartbeat"
mkdir -p "$LOG_DIR"
chmod 700 "$JARVIS_HOME"

# Common install locations, since services start with a minimal PATH.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

# Load host settings (non-secret). Must be owned by you and not world-writable.
if [ -f "$JARVIS_HOME/host.env" ]; then
  # shellcheck disable=SC1091
  . "$JARVIS_HOME/host.env"
fi

# Load secrets from ~/.openjarvis/.env (KEY=value lines; parsed, never executed).
ENV_FILE="${OPENJARVIS_HOME:-$HOME/.openjarvis}/.env"
if [ -f "$ENV_FILE" ]; then
  perms=$(stat -c '%a' "$ENV_FILE" 2>/dev/null || stat -f '%Lp' "$ENV_FILE")
  case "$perms" in
    600|400) ;;
    *) echo "Refusing to load $ENV_FILE: permissions are $perms. Run: chmod 600 $ENV_FILE"; exit 78 ;;
  esac
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line#"${line%%[![:space:]]*}"}"          # trim leading spaces
    case "$line" in ''|'#'*) continue ;; esac
    key="${line%%=*}"; val="${line#*=}"
    case "$key" in *[!A-Za-z0-9_]*|'') continue ;; esac
    [ "$key" = "$line" ] && continue                   # no '='
    val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"
    [ -n "$val" ] && export "$key=$val"
  done < "$ENV_FILE"
fi

# Simple log cap: truncate a log file once it passes ~10 MB.
for f in "$LOG_DIR"/*.log; do
  [ -f "$f" ] || continue
  size=$(wc -c < "$f" | tr -d ' ')
  if [ "$size" -gt 10485760 ]; then : > "$f"; fi
done

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

if [ -z "${JARVIS_CMD:-}" ]; then
  log "Jarvis host is up (placeholder mode: no JARVIS_CMD set yet)."
  child=""
else
  log "Starting: $JARVIS_CMD"
  bash -c "$JARVIS_CMD" &
  child=$!
fi

stop() {
  log "Stop requested."
  if [ -n "$child" ]; then
    kill -TERM "$child" 2>/dev/null
    wait "$child" 2>/dev/null
  fi
  exit 0
}
trap stop TERM INT

while :; do
  if [ -n "$child" ] && ! kill -0 "$child" 2>/dev/null; then
    wait "$child"; code=$?
    log "Agent exited with code $code; the service manager will restart it."
    exit "${code:-1}"
  fi
  date +%s > "$HEARTBEAT"
  sleep 30 &
  wait $!
done
