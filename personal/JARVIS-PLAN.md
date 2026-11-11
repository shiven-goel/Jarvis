# My Jarvis: plan and decisions

This repo is a **fork of [open-jarvis/OpenJarvis](https://github.com/open-jarvis/OpenJarvis)**
(Apache-2.0). Everything upstream is unchanged. All personal customization lives in `personal/`,
plus clearly marked small patches where a feature has to be added to OpenJarvis itself.
OpenJarvis is tracked as the `upstream` remote, so its updates can be pulled in.

## Decisions (from the Q&A)

| Topic | Decision |
|-------|----------|
| Base | Fork OpenJarvis and customize on top |
| Host | Linux PC / old laptop, running 24/7 (hardware TBD: run `personal/host/check-hardware.sh`) |
| Brain | **Hybrid**: local Qwen via Ollama for quick and private tasks; Claude and Gemini via API for complex ones |
| Privacy | **Everything stored locally.** Anything touching email, Drive, Calendar or Notion is answered by the **local** model only; cloud models get general questions only |
| Channels | **Slack** (Socket Mode) and **WhatsApp** via the Baileys bridge on a **separate spare number** |
| Data (read) | Gmail, Google Calendar + Drive, Notion, Outlook (personal now; work only after IT approval) |
| Autonomy | **Auto for low-risk** (search, notes, reminders, reading). **Ask first** for send, delete, share, pay, book, or run code |
| Voice | ElevenLabs voice replies |
| Interface | **Mix**: OpenJarvis features and screens, with a new HUD-style main screen (glowing reactive orb, chat log, status panel) and an original Jarvis theme |
| Remote access | Tailscale only. Nothing exposed to the public internet |
| Users | Me + family/team. Family gets **chat only, with separate memory**; no access to my data connectors |
| Telemetry | No preference given, so the private default: analytics **off**, learning loop **off** (local traces kept) |
| Skill level | Beginner: copy-paste steps, everything explained |

## What OpenJarvis already gives us

- `jarvis serve` API on `127.0.0.1:8000` (OpenAI-compatible `/v1/chat/completions`, `/v1/agents/events` websocket, `/health`, `/v1/channels/*`, connectors, approvals)
- Local engines (Ollama), cloud providers (Anthropic, Google, OpenAI) via extras
- Persona and memory files: `~/.openjarvis/SOUL.md`, `USER.md`, `MEMORY.md`; SQLite FTS5 memory at `~/.openjarvis/memory.db`
- Skills (agentskills.io format, Hermes/OpenClaw sources) in `~/.openjarvis/skills/`
- Slack and WhatsApp Baileys channels; Gmail, GDrive, Calendar, Notion and Outlook connectors
- TTS backends including `elevenlabs`; speech-to-text via faster-whisper (local)
- Security: secret/PII scanners, SSRF protection, audit log, capability policies, a loopback-only server by default
- A systemd unit example, a web frontend (Vite/React), and a Tauri desktop app

## Gaps I found in the code that need building (must-haves)

These came from reading the source, not guessing:

1. **No sender allowlist on Slack or WhatsApp.** `channels/slack.py` and the Baileys `bridge.ts` pass every
   message from anyone to the agent, including WhatsApp groups. → Add an allowlist (Slack user IDs, WhatsApp numbers),
   ignore groups, and map each allowed person to a role (`owner` or `family`).
2. **Only one channel at a time.** `jarvis serve` starts a single `channel.default_channel`. → Start Slack and WhatsApp together.
3. **Tool confirmation isn't enforced.** `security.enforce_tool_confirmation` is accepted but not applied
   (per the docs). The approvals API only serves the proactive agent. → Add a risk-tiered approval gate
   (low = auto; high = ask on the same channel with a "yes"/"no" reply, or in the dashboard).
4. **No sensitivity-based routing.** The router picks a model by query complexity, not by privacy.
   → Add a rule: if connector data (mail, files, Notion, calendar) is in context, or a tool returns it, use the local model.
5. **One shared memory.** A single `memory.db` and `USER.md`. → Add per-person memory and profile for family users,
   and block connector tools for anyone who isn't the owner.

## Other things you need because of external apps

- **WhatsApp (Baileys)**: unofficial. Use a **spare SIM or eSIM** with WhatsApp installed on a phone, linked to Jarvis by
  scanning a QR code. Open WhatsApp on that phone every couple of weeks, or the linked device logs out.
  The number could get banned. That's why it's not your main number. Needs Node.js 22+.
- **Slack**: an app built from a manifest, with Socket Mode (`xoxb-` bot token and `xapp-` app token). No public URL.
  Only add the scopes we need.
- **Gmail least-privilege**: an IMAP app password gives **full mailbox access**. Prefer Google OAuth with the read-only scope
  (`gmail.readonly`) through the same Google Cloud project as Calendar and Drive.
  - Google OAuth apps in "Testing" mode have refresh tokens that **expire after 7 days**, so reconnecting weekly is expected.
  - OpenJarvis's Drive token refresh is "coming soon" upstream.
- **Outlook work account**: many companies block IMAP and app passwords, and policy may forbid it. Ask IT first.
- **Prompt injection**: emails and documents can contain text like "ignore your instructions and forward…".
  Connector content is treated as untrusted data, and high-risk actions *always* need your approval. This is one more
  reason for gap #3.
- **Secrets**: all keys live in `~/.openjarvis/.env` (`chmod 600`), never in the repo. `.env` is git-ignored.
  WhatsApp session files (`~/.openjarvis/whatsapp_auth`) are as sensitive as a password.
- **Backups**: `~/.openjarvis` (memory, chats, tokens) is backed up nightly to an **encrypted** archive.
- **Disk encryption trade-off**: full-disk encryption (LUKS) protects data if the laptop is stolen, but needs a password
  at every boot, which breaks unattended restarts. Decision deferred to Phase 1.
- **Tailscale**: the dashboard stays bound to `127.0.0.1` and is published to your tailnet only (`tailscale serve`).
- **License**: keep OpenJarvis's `LICENSE` and copyright notices; modified files are marked as changed.

## Accounts and costs (upfront)

| Need | Account | Cost |
|------|---------|------|
| Claude | console.anthropic.com API key | Pay-as-you-go (billing required) |
| Gemini | aistudio.google.com API key | Free tier, then pay-as-you-go |
| Local Qwen | Ollama | Free |
| Slack | Workspace where you can install apps | Free plan OK |
| WhatsApp | Spare SIM or eSIM plus a phone to link once | Cost of the SIM |
| Google (Gmail, Calendar, Drive) | Google Cloud project with an OAuth client | Free |
| Notion | Internal integration token | Free |
| Outlook | App password (personal account) | Free |
| ElevenLabs | API key | Free tier (limited); about $5/mo after |
| Tailscale | Personal plan | Free |

## Build phases (each one tested before the next)

| Phase | What | Status |
|-------|------|--------|
| 0 | Fork imported, plan written, host scripts adapted | ✅ done |
| 1 | Host: Ubuntu setup (no sleep, SSH, firewall, auto-updates, systemd service, health check). Install OpenJarvis from this repo, then Ollama + Qwen | next |
| 2 | Brain: Claude + Gemini keys in `.env`; hybrid config; **privacy router** (gap 4) | |
| 3 | Identity + memory: `SOUL.md` (Jarvis persona), `USER.md` (you), memory on; **per-person memory** (gap 5) | |
| 4 | Safety: **approval gate** (gap 3), capability policy, audit log, backups | |
| 5 | Data: Google OAuth read-only (Gmail, Calendar, Drive), Notion, Outlook | |
| 6 | Channels: Slack + WhatsApp **with allowlist** (gap 1), **both at once** (gap 2) | |
| 7 | Voice: ElevenLabs replies (audio in Slack/WhatsApp/dashboard); local Whisper for voice notes | |
| 8 | Skills: starter skills (morning brief, inbox triage, meeting prep) + how to add your own | |
| 9 | Interface: Jarvis HUD theme + orb main screen inside the OpenJarvis frontend; Tailscale access | |

## Repo layout

```
(upstream OpenJarvis files, untouched)
personal/
  JARVIS-PLAN.md          this file
  host/
    check-hardware.sh     what's in my machine + recommended Qwen size
    linux/setup.sh        always-on Ubuntu setup + systemd user service
    linux/jarvis.service  service template
    run-jarvis.sh         supervisor wrapper (runs `jarvis serve`)
    healthcheck.sh        PASS/FAIL health report
    uninstall.sh          remove the service
```

## Pulling upstream updates later

```bash
git fetch upstream
git merge upstream/main     # personal/ never conflicts; patched files might
```
