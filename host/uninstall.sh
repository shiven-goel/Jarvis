#!/usr/bin/env bash
# Stops and removes the Jarvis auto-start service. Keeps ~/.jarvis (your data).
set -uo pipefail
case "$(uname -s)" in
  Linux)
    systemctl --user disable --now jarvis.service 2>/dev/null
    rm -f "$HOME/.config/systemd/user/jarvis.service"
    systemctl --user daemon-reload ;;
  Darwin)
    launchctl bootout "gui/$(id -u)/com.jarvis.agent" 2>/dev/null
    rm -f "$HOME/Library/LaunchAgents/com.jarvis.agent.plist" ;;
esac
echo "Jarvis service removed. Your data in ~/.jarvis was kept."
