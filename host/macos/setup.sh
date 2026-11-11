#!/usr/bin/env bash
# Step 1 (macOS): prepare this Mac to run Jarvis 24/7.
# Run as your normal user (NOT root). It asks for your password for sudo.
# Safe to run more than once.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
JARVIS_HOME="$HOME/.jarvis"
PLIST="$HOME/Library/LaunchAgents/com.jarvis.agent.plist"
ok()   { printf '\033[36m[ok]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[!!]\033[0m %s\n' "$*"; }

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as your normal user, not with sudo. It will ask for sudo when needed."; exit 1
fi

echo "== 1/5 Developer tools (git, curl) =="
if ! xcode-select -p >/dev/null 2>&1; then
  xcode-select --install || true
  warn "A window opened to install Command Line Tools. Finish it, then re-run this script."; exit 1
fi
ok "Command Line Tools present."

echo "== 2/5 Never sleep, restart after power loss =="
sudo pmset -a sleep 0 disksleep 0 displaysleep 10
sudo pmset -a autorestart 1 2>/dev/null || true   # Mac mini/desktops only
sudo pmset -a womp 1 2>/dev/null || true          # wake on network
ok "Sleep disabled (display still turns off after 10 min)."

echo "== 3/5 Firewall and SSH =="
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --setglobalstate on >/dev/null
ok "Firewall on."
if sudo systemsetup -setremotelogin on >/dev/null 2>&1; then
  ok "Remote Login (SSH) on."
else
  warn "Turn on SSH manually: System Settings > General > Sharing > Remote Login."
fi

echo "== 4/5 Jarvis folders =="
mkdir -p "$JARVIS_HOME/logs"
chmod 700 "$JARVIS_HOME"
if [ ! -f "$JARVIS_HOME/host.env" ]; then
  cat > "$JARVIS_HOME/host.env" <<'ENV'
# Jarvis host settings (no secrets here; API keys go in ~/.hermes/.env in Step 3).
# Step 2 will set the command that runs the agent, e.g.:
# JARVIS_CMD="hermes gateway"
ENV
fi
chmod 600 "$JARVIS_HOME/host.env"
chmod +x "$REPO/host/run-jarvis.sh" "$REPO/host/healthcheck.sh"
ok "Created $JARVIS_HOME (private to you)."

echo "== 5/5 Auto-start service (launchd agent) =="
mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s#__REPO__#$REPO#g" -e "s#__HOME__#$HOME#g" "$REPO/host/macos/com.jarvis.agent.plist" > "$PLIST"
plutil -lint "$PLIST" >/dev/null
launchctl bootout "gui/$(id -u)/com.jarvis.agent" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/com.jarvis.agent"
launchctl kickstart -k "gui/$(id -u)/com.jarvis.agent"
ok "com.jarvis.agent installed and started."

echo
warn "Do this once by hand so Jarvis starts after a reboot without you logging in:"
echo "     System Settings > Users & Groups > Automatically log in as: <you>"
echo "     (Not offered if FileVault is on. Then you must log in once after each reboot.)"
echo "     MacBook: keep the lid open, or plug in power + an external display."
echo
echo "Done. Next: run  $REPO/host/healthcheck.sh  then reboot and run it again."
