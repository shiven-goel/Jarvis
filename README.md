# Jarvis

A personal AI agent that runs 24/7 on a machine you own. It uses an existing model
(Claude, OpenAI or Gemini) through the open-source **Hermes Agent** harness by Nous Research,
and has long-term memory, skills, your tools, Slack, ElevenLabs voice, and a HUD dashboard.
No model training involved.

## Plan

| # | Step | What you get | Status |
|---|------|--------------|--------|
| 1 | **Home** | Always-on host: no sleep, SSH, firewall, auto-start at boot, auto-restart, health check | ✅ ready ([guide](docs/step-1-home.md)) |
| 2 | Agent harness | Hermes Agent installed and run by the Step 1 service | next |
| 3 | Brain | Pick Claude / OpenAI / Gemini, switch with one command, keys in `.env` | |
| 4 | Identity | `USER.md` (who you are) and `SOUL.md` (name, tone, behavior) | |
| 5 | Long-term memory | Facts and preferences kept across sessions | |
| 6 | Skills | Starter skills plus a template for adding your own | |
| 7 | Tools | Gmail with least-privilege scopes; asks before sending or deleting | |
| 8 | Channels | Slack (Socket Mode, only you allowed) and ElevenLabs voice replies | |
| 9 | Jarvis look | Dark cyan HUD dashboard: arc-reactor orb, chat log, status panel | |

## Accounts and keys you'll need (tell-you-upfront list)

| Needed for | Account | Cost |
|-----------|---------|------|
| Step 3 | At least one model API key: Anthropic (console.anthropic.com), OpenAI (platform.openai.com) or Google AI Studio (aistudio.google.com) | Pay-as-you-go. Anthropic and OpenAI require billing; Gemini has a limited free tier |
| Step 7 | Google Cloud project with an OAuth client, for Gmail | Free |
| Step 8 | Slack workspace you can install apps into | Free plan works |
| Step 8 | ElevenLabs API key | Free tier (limited characters); paid plans from about $5/mo |
| Step 1 | Nothing | Free |

## Security rules this project follows

- Secrets live only in `.env` files that are git-ignored and `chmod 600`. Never in code.
- Jarvis runs as **your normal user**, never as root.
- Inbound firewall allows SSH only. Nothing is exposed to the internet; Slack uses outbound Socket Mode.
- Tools get the smallest permissions that work; destructive actions (send, delete, share) need your approval.

## Layout

```
host/                 Step 1: always-on host
  linux/setup.sh      Ubuntu/Debian setup (systemd)
  macos/setup.sh      macOS setup (launchd)
  run-jarvis.sh       supervisor wrapper the service runs
  healthcheck.sh      PASS/FAIL health report
  uninstall.sh        remove the service (keeps your data)
docs/                 step-by-step guides
```
