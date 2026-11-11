# Microsoft 365 / Outlook mail: add as many accounts as you want

Jarvis can read mail from **several** Microsoft accounts at once:

- a personal **Outlook.com / Hotmail** account
- one or more **work or school Microsoft 365** accounts, even from different companies
- **shared mailboxes** you already have access to (e.g. `support@company.com`)

It is **read-only**: Jarvis can list, search and read mail, but it cannot send, move or delete
anything. It uses Microsoft Graph with the official sign-in flow.

> **Why not the built-in `outlook` connector?** That one uses IMAP with an app password.
> Microsoft has switched off that kind of login for Microsoft 365 and Outlook.com, and it only
> supports one mailbox. Use `jarvis m365` instead.

**Cost:** free. **Time:** about 15 minutes the first time, then 1 minute per extra account.

---

## Step 1: Register your own Microsoft "app" (one time)

This gives Jarvis an identity that Microsoft will let you sign in to. You only do this once,
no matter how many mailboxes you add.

1. Go to **https://entra.microsoft.com** and sign in.
   - If you only have a personal Microsoft account, Microsoft may ask you to create a free Azure
     account or tenant first. That's free and needs no paid subscription.
   - Or ask your company's IT team to register it in your work tenant (see "Work accounts" below).
2. Open **App registrations → New registration**.
   - **Name:** `Jarvis (personal)`
   - **Supported account types:** *Accounts in any organizational directory and personal Microsoft accounts*
     (this is what lets one app read both work and personal mail)
   - **Redirect URI:** leave empty
   - Click **Register**.
3. Copy the **Application (client) ID**. It looks like `1a2b3c4d-....`
4. Open **Authentication**, find **Allow public client flows**, set it to **Yes**, and click **Save**.
5. Open **API permissions → Add a permission → Microsoft Graph → Delegated permissions** and add:
   - `Mail.Read`
   - `User.Read`
   - `offline_access`
   - `Mail.Read.Shared` (only if you want shared mailboxes)

   Do **not** add `Mail.Send`, `Mail.ReadWrite` or anything with "Write". Jarvis doesn't need them.
6. You do **not** need a client secret. Don't create one.

Now save the ID on the Jarvis machine:

```bash
cd ~/jarvis
uv run jarvis m365 setup --client-id PASTE-THE-ID-HERE
```

## Step 2: Add each mailbox

Give each account a short name: lowercase, no spaces.

```bash
# your work account
uv run jarvis m365 add work

# your personal Outlook.com account
uv run jarvis m365 add personal --tenant consumers

# a second company
uv run jarvis m365 add acme --tenant acme.com
```

Each `add` prints something like:

```
1. Open https://microsoft.com/devicelogin on any device
2. Enter the code: ABCD-EFGH
3. Sign in with the account you want to add and accept the read-only permissions.
```

Do that on your phone or laptop. The terminal finishes by itself.

### Shared mailboxes

Re-add the account that has access with `--shared-access`, then add the shared mailbox through it:

```bash
uv run jarvis m365 add work --shared-access
uv run jarvis m365 add-shared support --via work --mailbox support@company.com
```

## Step 3: Check it works

```bash
uv run jarvis m365 list         # every account and address
uv run jarvis m365 test         # 3 newest emails across all accounts
uv run jarvis m365 test work    # just one account
```

✅ You should see your latest emails, each tagged with its account name, like `[work]` or `[personal]`.

Then ask Jarvis (in Slack, WhatsApp or the dashboard):
- "What are my unread emails?"
- "Search my work email for the Q3 budget"
- "Anything from my bank in my personal inbox this week?"

These go to your **local** model only, because they mention email (see `personal/docs/FIXES.md`).

## Remove an account

```bash
uv run jarvis m365 remove acme
```

This deletes its saved sign-in. To also revoke Jarvis's access on Microsoft's side, go to
https://myapps.microsoft.com (work) or https://account.live.com/consent/Manage (personal) and
remove "Jarvis (personal)".

## Work accounts: things that can block you

| Message | Meaning | Fix |
|---------|---------|-----|
| "requires an admin to approve" / `AADSTS65001` | Your company doesn't let people approve apps themselves | Ask IT to grant admin consent for `Mail.Read`, `User.Read` |
| `AADSTS7000218` | Public client flows are off | Step 1.4 |
| `AADSTS50020` / `AADSTS50194` | Wrong account type on the app | Step 1.2: choose "any organizational directory and personal accounts" |
| Device code sign-in blocked | Company policy blocks this sign-in method | Ask IT, or don't connect that account |
| "don't have permission to read this mailbox" | No access to the shared mailbox | Ask the mailbox owner or IT |

**Check your company's policy** before connecting a work mailbox to a personal assistant.
Some employers forbid it.

## Where things are stored

| What | Where | Protection |
|------|-------|------------|
| App ID | `~/.openjarvis/connectors/m365/app.json` | mode 600 |
| Accounts list | `~/.openjarvis/connectors/m365/accounts.json` | mode 600 |
| Sign-in tokens | `~/.openjarvis/connectors/m365/tokens/<name>.json` | mode 600, folder 700 |

Treat the tokens folder like a password. Anyone who copies it can read those inboxes until
you revoke access.
