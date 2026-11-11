#!/usr/bin/env bash
# Step 1 (Linux: Ubuntu/Debian): prepare this machine to run Jarvis 24/7.
# Run as your normal user (NOT root). It asks for your sudo password.
# Safe to run more than once.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
JARVIS_HOME="$HOME/.jarvis"
ok()   { printf '\033[36m[ok]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[!!]\033[0m %s\n' "$*"; }

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as your normal user, not with sudo. It will ask for sudo when needed."; exit 1
fi

echo "== 1/5 Packages and automatic security updates =="
if command -v apt-get >/dev/null; then
  sudo apt-get update -y
  sudo apt-get install -y curl git openssh-server unattended-upgrades ufw
  echo 'unattended-upgrades unattended-upgrades/enable_auto_updates boolean true' | sudo debconf-set-selections
  sudo dpkg-reconfigure -f noninteractive unattended-upgrades
  ok "Packages installed; security updates install automatically."
else
  warn "Not a Debian/Ubuntu system. Install curl, git, openssh-server and a firewall yourself, then re-run."
fi

echo "== 2/5 Never sleep, ignore laptop lid =="
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null
sudo mkdir -p /etc/systemd/logind.conf.d
sudo tee /etc/systemd/logind.conf.d/jarvis.conf >/dev/null <<'CONF'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
CONF
ok "Sleep disabled. Lid setting takes effect after a reboot."

echo "== 3/5 SSH (remote access) and firewall =="
sudo systemctl enable --now ssh 2>/dev/null || sudo systemctl enable --now sshd
if command -v ufw >/dev/null; then
  sudo ufw allow OpenSSH >/dev/null
  sudo ufw --force enable >/dev/null
  ok "Firewall on: only SSH is allowed in."
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

echo "== 5/5 Auto-start service (systemd user service) =="
mkdir -p "$HOME/.config/systemd/user"
sed "s#__REPO__#$REPO#g" "$REPO/host/linux/jarvis.service" > "$HOME/.config/systemd/user/jarvis.service"
# Linger = your user services start at boot, even before you log in.
sudo loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable jarvis.service >/dev/null
systemctl --user restart jarvis.service
ok "jarvis.service installed, enabled at boot, and started."

echo
echo "Done. Next: run  $REPO/host/healthcheck.sh  then reboot and run it again."
