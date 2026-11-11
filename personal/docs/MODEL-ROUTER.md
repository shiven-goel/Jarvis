# Model router: which AI answers which message

Every message to Jarvis (Slack, WhatsApp or the dashboard) goes through the router. It picks the
**cheapest model that is good enough** for that message, with a hard rule that **private data only ever
goes to a local model**.

## How it decides, step by step

1. **Private?** If the message mentions email, calendar, files, Notion and so on, or the chat touched
   private data in the last 6 turns, only **local** models are allowed. Personal-data tools are also
   removed whenever a cloud model answers.
2. **What kind of task?** The router sorts each message into one of eight task types. Each type needs a
   minimum quality tier:

   | Task | Example | Tier needed |
   |------|---------|-------------|
   | chat | "hey", "thanks" | 1 |
   | quick | "what's the capital of Peru?" | 1 |
   | extract | "pull all the dates out as JSON" | 2 |
   | summarize | "summarize this article" (or pasted long text) | 2 |
   | writing | "write a cover letter" | 3 |
   | coding | "fix this Python bug" | 3 |
   | analysis | "compare these two offers" | 3 |
   | hard | "design… explain the trade-offs step by step" | 4 |

   A very complex message moves up one tier. A trivial quick, chat or extract message moves down one.
3. **How big?** It estimates the tokens: your message, the chat history so far, about 1,500 tokens of
   instructions and tools, and the expected length of the answer. Models whose context window is too
   small are skipped. That's how long documents get to long-context models automatically.
4. **Cheapest wins.** Among the models that qualify, it takes the one with the lowest estimated cost.
5. **Daily budget.** Past 80% of `daily_budget_usd`, tier-4 models stop being used. At 100%, only local
   models are used until midnight. Spending is measured from the real token counts each model reports.
6. **Fallbacks.** If the chosen model fails (outage, rate limit), Jarvis tries up to two backups. A private
   message only ever falls back to another local model.

## Try it without sending anything

```bash
uv run jarvis people route "fix this python bug: KeyError in my parser"
uv run jarvis people route "summarize the history of Rome"
uv run jarvis people route "what's on my calendar tomorrow?"
uv run jarvis people route "hi" --as mom
uv run jarvis people route "summarize this" --history-tokens 20000
```

Example output:

```
Model:    claude-sonnet-5-5 (cloud)
Why:      preferred for coding
Task:     coding (needs tier 3)
Tokens:   ~1515 in / ~1500 out
Est cost: $0.01803
Fallback: gemini-2.5-pro, claude-opus-5-5, qwen3:8b
Private:  False; personal-data tools removed
```

With the example config:

| Message | Goes to | Why |
|---------|---------|-----|
| "hey" / quick facts | local Qwen | tier 1, free |
| "summarize the history of Rome" | Claude Haiku 5.5 | cheapest tier-2 model |
| "write a cover letter" | Gemini 2.5 Pro | cheapest tier-3 model |
| "fix this Python bug" | Claude Sonnet 5.5 | `[router.prefer] coding` |
| hard multi-step reasoning | Claude Opus 5.5 | only tier-4 model |
| "check my inbox…" | local Qwen | private, so local only |
| "/claude what's on my calendar?" | local Qwen | private beats `/claude` |
| short message + very long chat | Claude Haiku 5.5 | too big for Qwen's 8K window |

## Choose a model yourself

Start a message with a model's alias: `/local`, `/haiku`, `/sonnet` (or `/claude`), `/opus`, `/flash`,
`/gemini`. Example: `/opus plan my week of workouts`. For private messages, cloud aliases are ignored.

## Change the behaviour

Everything is in `~/.openjarvis/people.toml` (copied from `personal/config/people.example.toml`):

- **`[router] daily_budget_usd`**: your daily cloud spending cap. `0` means no limit.
- **`[router.tasks]`**: minimum tier per task. For example, set `writing = 2` to let Haiku draft emails.
- **`[router.prefer]`**: always use a specific model for a task when it qualifies, e.g. `coding = "claude-sonnet-5-5"`.
- **`[[router.models]]`**: the catalog. Add, remove or disable models (`enabled = false`), and edit prices,
  context windows, tiers and aliases. You need at least one `kind = "local"` model.
  - Add a second local model, e.g. a faster `qwen3:4b` at tier 1, and the router uses whichever qualifies.
- **`[models] cloud_enabled = false`**: turns off every cloud model.

Run `uv run jarvis people check` after editing. It prints the catalog, or tells you what's wrong.

## Prices

The prices in the example are **USD per 1 million tokens**:

| Model | Input | Output | Context |
|-------|-------|--------|---------|
| Claude Haiku 5.5 | $0.10 ($0.50 above 100K input) | $0.50 ($2.50 above 100K) | 1M |
| Claude Sonnet 5.5 | $2.00 | $10.00 | 1M |
| Claude Opus 5.5 | $4.00 | $20.00 | 1M |
| Gemini 2.5 Flash / Pro | edit to current prices | | 1M |

The Claude prices are Anthropic's list prices as of November 2026. **Check the Gemini prices** at
ai.google.dev/pricing and update the file. The budget is only as accurate as these numbers.

## See what it's doing and spending

- `uv run jarvis people usage`: spend per model per day, for the last 7 days
- `http://127.0.0.1:8000/v1/jarvis/usage`: the same as JSON, plus the catalog (for the HUD)
- `http://127.0.0.1:8000/v1/jarvis/routes`: the last 50 decisions, with task, tokens, estimated cost and
  fallbacks (no message text)
- Usage is stored in `~/.openjarvis/router_usage.json` (mode 600) and kept for 60 days.

## One setting for the local model

Ollama's default context window is small. The example config assumes **8,192 tokens**. Set Ollama to match:

- **Linux:** `sudo systemctl edit ollama`, add these two lines, then `sudo systemctl restart ollama`:
  ```
  [Service]
  Environment="OLLAMA_CONTEXT_LENGTH=8192"
  ```
- **Mac (Ollama app):** `launchctl setenv OLLAMA_CONTEXT_LENGTH 8192`, then quit and reopen Ollama.

If you raise it (more RAM needed), raise `context_window` for that model in `people.toml` to match.

## Limits to know about

- Task types are worked out from keywords and length, not by an AI. It's fast and free, but sometimes
  wrong. Use a `/model` prefix or `[router.prefer]` to correct it.
- Token counts before sending are **estimates** (about 4 characters per token). The budget itself uses the
  real counts each model reports afterwards.
- On the dashboard, a model you pick in the UI is respected unless the message is private. The router only
  chooses when the request doesn't name a model.

**Code:** `src/openjarvis/household/model_router.py` (router, catalog, usage tracker) and
`src/openjarvis/household/privacy.py` (privacy rules on top). **Tests:** `tests/household/test_model_router.py`.
