#!/usr/bin/env bash
# Checks that the Jarvis host is healthy. Prints PASS/FAIL per check.
set -uo pipefail
JARVIS_HOME="${JARVIS_HOME:-$HOME/.jarvis}"
fails=0
pass() { printf '\033[36mPASS\033[0m %s\n' "$*"; }
fail() { printf '\033[31mFAIL\033[0m %s\n' "$*"; fails=$((fails+1)); }

case "$(uname -s)" in
  Linux)
    if systemctl --user is-active --quiet jarvis.service; then pass "service is running"; else fail "service not running (systemctl --user status jarvis)"; fi
    if systemctl --user is-enabled --quiet jarvis.service; then pass "service starts at boot"; else fail "service not enabled"; fi
    if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" = "yes" ]; then pass "linger on (starts before login)"; else fail "linger off (sudo loginctl enable-linger $USER)"; fi
    if systemctl is-enabled suspend.target 2>/dev/null | grep -q masked; then pass "sleep disabled"; else fail "sleep not disabled"; fi
    LOGCMD="journalctl --user -u jarvis -n 20 --no-pager"
    ;;
  Darwin)
    if launchctl print "gui/$(id -u)/com.jarvis.agent" 2>/dev/null | grep -q 'state = running'; then pass "service is running"; else fail "service not running"; fi
    if [ "$(pmset -g | awk '$1=="sleep"{print $2}')" = "0" ]; then pass "sleep disabled"; else fail "system sleep is on"; fi
    LOGCMD="tail -n 20 $JARVIS_HOME/logs/jarvis.log"
    ;;
  *) fail "unsupported OS"; LOGCMD="true" ;;
esac

if [ -f "$JARVIS_HOME/heartbeat" ]; then
  age=$(( $(date +%s) - $(cat "$JARVIS_HOME/heartbeat") ))
  if [ "$age" -lt 120 ]; then pass "heartbeat ${age}s ago"; else fail "heartbeat is stale (${age}s old)"; fi
else
  fail "no heartbeat file yet (wait 30s and retry)"
fi

perm=$(ls -ld "$JARVIS_HOME" 2>/dev/null | cut -c1-10)
if [ "$perm" = "drwx------" ]; then pass "$JARVIS_HOME is private"; else fail "$JARVIS_HOME permissions are $perm (want drwx------)"; fi

OJ="${OPENJARVIS_HOME:-$HOME/.openjarvis}"
for f in "$OJ/.env" "$OJ/people.toml"; do
  if [ -f "$f" ]; then
    p=$(stat -c '%a' "$f" 2>/dev/null || stat -f '%Lp' "$f")
    if [ "$p" = "600" ] || [ "$p" = "400" ]; then pass "$(basename "$f") is private"; else fail "$f permissions are $p (run: chmod 600 $f)"; fi
  fi
done
if [ -f "$OJ/people.toml" ]; then pass "allowlist (people.toml) present"; else fail "no $OJ/people.toml: anyone who can reach a channel can talk to Jarvis"; fi

echo; echo "Recent log:"; $LOGCMD 2>/dev/null || true
echo
if [ "$fails" -eq 0 ]; then echo "All checks passed."; else echo "$fails check(s) failed."; exit 1; fi
