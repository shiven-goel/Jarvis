# What I fixed in OpenJarvis (and how to check each fix)

These are the gaps found by reading OpenJarvis's source. Each one is now fixed in this fork and covered
by automated tests. All changes are marked `[jarvis-fork]` in the code, so they're easy to find when
merging upstream updates.

**The switch:** every fix below turns on when `~/.openjarvis/people.toml` exists. Without that file,
OpenJarvis behaves exactly like upstream.

```bash
cp ~/jarvis/personal/config/people.example.toml ~/.openjarvis/people.toml
chmod 600 ~/.openjarvis/people.toml
nano ~/.openjarvis/people.toml        # put in your Slack ID and WhatsApp number
uv run jarvis people check           # must end with "people.toml OK"
```

---

## 1. Only people you list can talk to Jarvis

**Before:** Slack and WhatsApp passed every message from anyone, including WhatsApp groups, to the agent.
Anyone who learned Jarvis's number could ask about your email.

**Now:**
- Senders not in `people.toml` are ignored. The log records their exact id so you can add them if you want:
  `journalctl --user -u jarvis | grep unknown`
- Group chats, WhatsApp broadcasts and public Slack channels are ignored. Only 1:1 chats and Slack DMs work.
- Each person has a role: `owner` (you, exactly one) or `family`.

**Code:** `src/openjarvis/household/config.py`, `src/openjarvis/household/wiring.py`

**Check it:** message Jarvis from a number or account that isn't listed. There's no reply, and a line
`Ignored message from unknown ...` appears in the log.

## 2. Slack and WhatsApp at the same time

**Before:** `jarvis serve` started only one channel (`default_channel`).

**Now:** list several, separated by commas: `default_channel = "slack,whatsapp_baileys"`.

**Code:** `SystemBuilder._resolve_channels` (`src/openjarvis/system/builder.py`), `src/openjarvis/cli/serve.py`

**Check it:** when `jarvis serve` starts, it prints one `Channel:` line for each channel.

## 3. Jarvis asks before doing anything risky

**Before:** the `enforce_tool_confirmation` setting existed but wasn't applied to chat or dashboard turns.

**Now (what you chose: "auto for low-risk"):**

| Kind of tool | Examples | What happens |
|--------------|----------|--------------|
| Low risk | search, weather, calculator, reading mail and files, memory, reminders, web GET | runs immediately |
| Everything else | sending messages, writing files, running code or shell commands, HTTP POST, git commit | Jarvis asks you first |

Approval requests look like this in the same chat:

```
🛡️ Approval needed [3fa2]
I want to run: channel_send
Details: {"to": "...", "text": "..."}
Reply "yes 3fa2" to allow or "no 3fa2" to cancel. No reply in 10 min = cancelled.
```

- A plain `yes` or `no` works when only one request is waiting.
- No answer means **no**.
- Family members can never approve anything; their risky requests are refused.
- Passwords, tokens and keys in the details are shown as `***`.
- On the dashboard, the same requests appear at `GET /v1/jarvis/approvals`, and you approve them with
  `POST /v1/jarvis/approvals/<id>/approve` (or `/deny`). The HUD will get buttons for this in Phase 9.
- Edit `[approval]` in `people.toml` to trust more tools (`extra_auto_tools`) or always ask for some (`always_ask_tools`).

**Code:** `src/openjarvis/security/approval_gate.py`, called from `ToolExecutor` (`src/openjarvis/tools/_stubs.py`)

**Check it:** in your DM with Jarvis, type: `create a file called test.txt saying hi`.
You should get an approval request. Answer `no`, and Jarvis should say it wasn't approved.

## 4. Private data never goes to a cloud model

**Before:** OpenJarvis chose a model by how hard the question was, not by privacy.

**Now:**
1. A message about email, calendar, files, Notion, contacts and similar (keyword list in `people.toml`)
   goes to the **local** model.
2. Once private data comes up, the conversation **stays local** for the next 6 turns, so follow-ups like
   "and the second one?" don't leak.
3. Simple requests also stay local, which is quick and free.
4. Complex, non-private requests go to **Claude**. Start a message with `/gemini` for Gemini, `/claude`
   to force Claude, or `/local` to force local.
5. When a cloud model answers, it **cannot use** any personal-data tools (mail, files, knowledge base,
   calendar), and **earlier private messages are removed** from the history it sees.
   This applies in Slack, WhatsApp **and** the dashboard.
6. `/claude` or `/gemini` is ignored when the message is about private data.

**Code:** `src/openjarvis/household/privacy.py`, `src/openjarvis/household/api_guard.py`, gate rule in `approval_gate.py`

**Check it:** after a few messages, open `http://127.0.0.1:8000/v1/jarvis/routes` on the Jarvis machine
(or run `curl -s http://127.0.0.1:8000/v1/jarvis/routes`). It lists the last 50 decisions, with no message
text, e.g. `{"person": "owner", "channel": "slack", "model": "qwen3:8b", "reason": "mentions private data"}`.
For a live log, run `jarvis --verbose serve`.

## 5. Family members get their own memory and no access to your data

**Before:** one shared memory, and your `USER.md` and `MEMORY.md` were part of every reply.

**Now each family member has:**
- their own folder, `~/.openjarvis/people/<id>/`, with their own `USER.md`, `MEMORY.md` and `memory.db` (only your user can read it)
- their own chat history
- only safe tools: calculator, weather, web search, and their own memory; **no** email, files, calendar or Notion
- instructions telling Jarvis it can't see or reveal anything private about you

**Check it:** have a family member ask "what's in Shiven's inbox?". Jarvis should say it has no access.

## 6. New: Microsoft 365 / Outlook with many accounts

See [`microsoft-365-setup.md`](microsoft-365-setup.md). Commands: `jarvis m365 setup | add | add-shared | list | test | remove`.
New tools: `m365_accounts`, `m365_mail_recent`, `m365_mail_search`, `m365_mail_read` (all read-only).
Mail is also indexed for search: `jarvis connect --sync`.

## Also fixed

- **Secrets file:** the always-on service now loads `~/.openjarvis/.env` itself. It refuses to start if
  the file can be read by other users, and it never runs the file as a script.
- **Health check:** `healthcheck.sh` now also checks that `.env` and `people.toml` are private and that the allowlist exists.
- **Dashboard API:** new `GET /v1/jarvis/household` endpoint for the HUD's status panel.

## Tests

```bash
uv run pytest tests/household tests/connectors/test_m365_mail.py -q    # 76 tests for these fixes
uv run pytest tests -q -n 8                                            # whole suite
```

Full-suite result when this was written: 9,467 passed. 22 tests fail, and they fail the same way on unmodified
upstream, because they need live network services (dataset downloads, Gemma.cpp, live connectors).

## Files changed in upstream code (keep these in mind when merging)

| File | Change |
|------|--------|
| `src/openjarvis/tools/_stubs.py` | calls the approval gate before each tool |
| `src/openjarvis/system/orchestrator.py` | optional per-request `model`, `tool_instances`, `memory_files`, `system_prompt_config`, `memory_backend` |
| `src/openjarvis/system/core.py` | passes those through; hands channels to the household layer |
| `src/openjarvis/system/builder.py` | `_resolve_channels` for several channels |
| `src/openjarvis/cli/serve.py` | starts and wires every channel |
| `src/openjarvis/server/routes.py` | privacy routing on `/v1/chat/completions` |
| `src/openjarvis/server/app.py` | mounts `/v1/jarvis/*` |
| `src/openjarvis/security/capabilities.py` | capability entries for the 4 `m365_*` tools |
| `src/openjarvis/{cli,tools,connectors}/__init__.py` | registers the new commands, tools and connector |

Everything else is in new files: `src/openjarvis/household/`, `security/approval_gate.py`,
`connectors/m365_mail.py`, `tools/m365_mail.py`, `cli/m365_cmd.py` and `cli/people_cmd.py`.
