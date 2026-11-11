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

## Gaps found in the code: ✅ all fixed

Details and how to verify each one: [`docs/FIXES.md`](docs/FIXES.md).

1. ✅ **Sender allowlist + roles** (owner / family); group chats and strangers ignored
2. ✅ **Several channels at once** (`default_channel = "slack,whatsapp_baileys"`)
3. ✅ **Risk-tiered approvals**: low risk runs automatically, everything else asks you in chat or on the dashboard
4. ✅ **Privacy router**: private data is only ever seen by the local model, in chat **and** on the dashboard
5. ✅ **Per-person memory**; family gets no access to your data
6. ✅ **New: Microsoft 365 / Outlook with many accounts** (work, personal, shared mailboxes); see [`docs/microsoft-365-setup.md`](docs/microsoft-365-setup.md)

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
- **Outlook**: Microsoft has switched off app-password IMAP, so the upstream `outlook` connector won't work.
  Use the new `jarvis m365` (Graph, read-only, many accounts). For work accounts, IT may need to approve the app,
  and company policy may forbid it. Ask first.
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
| 0b | All 5 gaps fixed + Microsoft 365 multi-account connector, with 76 tests | ✅ done |
| 1 | Host: Ubuntu setup (no sleep, SSH, firewall, auto-updates, systemd service, health check). Install OpenJarvis from this repo, then Ollama + Qwen | next |
| 2 | Brain: Claude + Gemini keys in `.env`; Ollama + Qwen (privacy router is built) | |
| 3 | Identity + memory: `SOUL.md` (Jarvis persona), `USER.md` (you); family memory is built | |
| 4 | Safety: approval gate ✅; capability policy, audit log, backups | |
| 5 | Data: Google OAuth read-only (Gmail, Calendar, Drive), Notion, Microsoft 365 (`jarvis m365`) | |
| 6 | Channels: create the Slack app, link the WhatsApp number (allowlist + both-at-once are built) | |
| 7 | Voice: ElevenLabs replies (audio in Slack/WhatsApp/dashboard); local Whisper for voice notes | |
| 8 | Skills: starter skills (morning brief, inbox triage, meeting prep) + how to add your own | |
| 9 | Interface: Jarvis HUD theme + orb main screen inside the OpenJarvis frontend; Tailscale access | |

## Repo layout

```
(upstream OpenJarvis files; patched spots are marked [jarvis-fork])
src/openjarvis/household/        allowlist, roles, privacy router, per-person memory, approvals API
src/openjarvis/security/approval_gate.py
src/openjarvis/connectors/m365_mail.py   + tools/m365_mail.py + cli/m365_cmd.py
src/openjarvis/cli/people_cmd.py         `jarvis people check`
tests/household/, tests/connectors/test_m365_mail.py
personal/
  JARVIS-PLAN.md          this file
  docs/FIXES.md           what was fixed + how to verify
  docs/microsoft-365-setup.md
  config/config.toml      -> ~/.openjarvis/config.toml
  config/people.example.toml -> ~/.openjarvis/people.toml (chmod 600)
  config/env.example      -> ~/.openjarvis/.env (chmod 600)
  host/                   always-on Ubuntu setup, service, health check
```

## Pulling upstream updates later

```bash
git fetch upstream
git merge upstream/main     # personal/ never conflicts; patched files might
```
