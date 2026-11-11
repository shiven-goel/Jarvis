"""Microsoft 365 / Outlook mail via Microsoft Graph: many accounts, read-only.

[jarvis-fork] Not part of upstream OpenJarvis.

Why: the upstream ``outlook`` connector uses IMAP with an app password.
Microsoft has turned off that kind of "basic auth" for Exchange Online (work /
school Microsoft 365) and for Outlook.com, and it supports one mailbox only.
This connector uses Microsoft Graph with OAuth (device-code sign-in, no web
server needed) and supports any number of accounts:

* personal Outlook.com / Hotmail accounts
* work or school Microsoft 365 accounts (several tenants are fine)
* shared mailboxes you have access to (``add-shared``)

Permissions are read-only delegated scopes: ``Mail.Read`` (+ ``Mail.Read.Shared``
only when you add shared mailboxes), ``User.Read`` and ``offline_access``.
Tokens live in ``~/.openjarvis/connectors/m365/`` with mode 0600.

Setup is done with ``jarvis m365 ...`` (see ``openjarvis/cli/m365_cmd.py``).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional
from urllib.parse import quote

import httpx

from openjarvis.connectors._stubs import BaseConnector, Document, SyncStatus
from openjarvis.core.registry import ConnectorRegistry
from openjarvis.tools._stubs import ToolSpec

logger = logging.getLogger(__name__)

GRAPH_URL = "https://graph.microsoft.com/v1.0"
LOGIN_URL = "https://login.microsoftonline.com"
BASE_SCOPES = ("offline_access", "User.Read", "Mail.Read")
SHARED_SCOPE = "Mail.Read.Shared"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"

_SELECT = (
    "id,subject,from,toRecipients,ccRecipients,receivedDateTime,bodyPreview,"
    "isRead,webLink,conversationId,hasAttachments"
)
_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_MAX_BODY_CHARS = 8000


class M365Error(RuntimeError):
    """A user-facing Microsoft 365 connector error."""


def validate_alias(alias: str) -> str:
    a = (alias or "").strip().lower()
    if not _ALIAS_RE.match(a):
        raise M365Error(
            "Account names use lowercase letters, digits, '-' or '_' "
            "(max 32 chars), e.g. 'work', 'personal', 'acme-sales'."
        )
    return a


def _config_dir() -> Path:
    from openjarvis.core.config import get_config_dir

    return Path(get_config_dir())


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class M365Store:
    """Files under ``<config>/connectors/m365``.

    app.json       {"client_id": ..., "tenant": "common"}
    accounts.json  {"accounts": {alias: {"login", "mailbox", "address", "kind", ...}}}
    tokens/<login>.json   OAuth tokens for one signed-in user
    """

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root else _config_dir() / "connectors" / "m365"

    # -- helpers --
    def _ensure(self) -> None:
        (self.root / "tokens").mkdir(parents=True, exist_ok=True)
        for d in (self.root, self.root / "tokens"):
            try:
                d.chmod(0o700)
            except OSError:
                pass

    def _read(self, path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text())
        except FileNotFoundError:
            return default
        except (OSError, json.JSONDecodeError) as exc:
            raise M365Error(f"Could not read {path}: {exc}") from exc

    def _write_private(self, path: Path, data: Any) -> None:
        self._ensure()
        tmp = path.with_suffix(path.suffix + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass

    # -- app registration --
    def load_app(self) -> Dict[str, str]:
        data = self._read(self.root / "app.json", {})
        client_id = os.environ.get("M365_CLIENT_ID", "").strip() or data.get(
            "client_id", ""
        )
        tenant = os.environ.get("M365_TENANT", "").strip() or data.get(
            "tenant", "common"
        )
        return {"client_id": client_id, "tenant": tenant or "common"}

    def save_app(self, client_id: str, tenant: str = "common") -> None:
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", client_id.strip()):
            raise M365Error("The Application (client) ID should look like a GUID.")
        self._write_private(
            self.root / "app.json",
            {"client_id": client_id.strip(), "tenant": tenant or "common"},
        )

    # -- accounts --
    def accounts(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._read(self.root / "accounts.json", {}).get("accounts", {}))

    def get_account(self, alias: str) -> Dict[str, Any]:
        acct = self.accounts().get(alias)
        if acct is None:
            known = ", ".join(sorted(self.accounts())) or "none yet"
            raise M365Error(
                f"No Microsoft 365 account named '{alias}' (have: {known})."
            )
        return acct

    def save_account(self, alias: str, entry: Dict[str, Any]) -> None:
        accts = self.accounts()
        accts[alias] = entry
        self._write_private(self.root / "accounts.json", {"accounts": accts})

    def remove_account(self, alias: str) -> bool:
        accts = self.accounts()
        entry = accts.pop(alias, None)
        if entry is None:
            return False
        self._write_private(self.root / "accounts.json", {"accounts": accts})
        login = entry.get("login", alias)
        if not any(a.get("login") == login for a in accts.values()):
            try:
                (self.root / "tokens" / f"{login}.json").unlink()
            except FileNotFoundError:
                pass
        return True

    # -- tokens --
    def load_token(self, login: str) -> Dict[str, Any]:
        return self._read(self.root / "tokens" / f"{login}.json", {})

    def save_token(self, login: str, data: Dict[str, Any]) -> None:
        self._write_private(self.root / "tokens" / f"{login}.json", data)


# ---------------------------------------------------------------------------
# Graph / OAuth client
# ---------------------------------------------------------------------------


@dataclass
class DeviceFlow:
    device_code: str
    user_code: str
    verification_uri: str
    message: str
    interval: int
    expires_at: float
    tenant: str
    scopes: str


class GraphClient:
    def __init__(
        self,
        store: Optional[M365Store] = None,
        *,
        http: Optional[httpx.Client] = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.store = store or M365Store()
        self._http = http or httpx.Client(timeout=30.0)
        self._clock = clock
        self._sleep = sleep

    # -- sign-in -----------------------------------------------------------------

    def _client_id(self) -> str:
        cid = self.store.load_app()["client_id"]
        if not cid:
            raise M365Error(
                "No Microsoft app registered yet. Run: "
                "jarvis m365 setup --client-id <id>"
            )
        return cid

    @staticmethod
    def scopes(shared: bool) -> str:
        return " ".join(BASE_SCOPES + ((SHARED_SCOPE,) if shared else ()))

    def start_device_flow(self, tenant: str, *, shared: bool = False) -> DeviceFlow:
        scopes = self.scopes(shared)
        r = self._http.post(
            f"{LOGIN_URL}/{tenant}/oauth2/v2.0/devicecode",
            data={"client_id": self._client_id(), "scope": scopes},
        )
        data = self._json(r, "start sign-in")
        return DeviceFlow(
            device_code=data["device_code"],
            user_code=data["user_code"],
            verification_uri=data.get(
                "verification_uri", "https://microsoft.com/devicelogin"
            ),
            message=data.get("message", ""),
            interval=int(data.get("interval", 5)),
            expires_at=self._clock() + int(data.get("expires_in", 900)),
            tenant=tenant,
            scopes=scopes,
        )

    def poll_device_flow(self, flow: DeviceFlow) -> Dict[str, Any]:
        interval = max(1, flow.interval)
        while self._clock() < flow.expires_at:
            self._sleep(interval)
            r = self._http.post(
                f"{LOGIN_URL}/{flow.tenant}/oauth2/v2.0/token",
                data={
                    "grant_type": DEVICE_GRANT,
                    "client_id": self._client_id(),
                    "device_code": flow.device_code,
                },
            )
            if r.status_code == 200:
                return self._token_record(r.json(), flow.tenant)
            err = self._safe_json(r).get("error", "")
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval += 5
                continue
            if err == "authorization_declined":
                raise M365Error("Sign-in was declined.")
            if err in ("expired_token", "bad_verification_code"):
                break
            raise M365Error(self._describe_error(r, "finish sign-in"))
        raise M365Error("The sign-in code expired. Run the command again.")

    def _token_record(self, data: Dict[str, Any], tenant: str) -> Dict[str, Any]:
        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", ""),
            "expires_at": self._clock() + int(data.get("expires_in", 3600)),
            "scope": data.get("scope", ""),
            "tenant": tenant,
        }

    def access_token(self, login: str, *, force_refresh: bool = False) -> str:
        tok = self.store.load_token(login)
        if not tok:
            raise M365Error(f"'{login}' is not signed in. Run: jarvis m365 add {login}")
        if not force_refresh and tok.get("expires_at", 0) - self._clock() > 120:
            return tok["access_token"]
        if not tok.get("refresh_token"):
            raise M365Error(
                f"'{login}' needs to sign in again: jarvis m365 add {login}"
            )
        r = self._http.post(
            f"{LOGIN_URL}/{tok.get('tenant', 'common')}/oauth2/v2.0/token",
            data={
                "grant_type": "refresh_token",
                "client_id": self._client_id(),
                "refresh_token": tok["refresh_token"],
                "scope": tok.get("scope") or self.scopes(False),
            },
        )
        if r.status_code != 200:
            err = self._safe_json(r).get("error", "")
            if err in ("invalid_grant", "interaction_required"):
                raise M365Error(
                    f"'{login}' sign-in expired or was revoked. "
                    f"Run: jarvis m365 add {login}"
                )
            raise M365Error(self._describe_error(r, "refresh sign-in"))
        new = self._token_record(r.json(), tok.get("tenant", "common"))
        if not new["refresh_token"]:
            new["refresh_token"] = tok["refresh_token"]
        new["username"] = tok.get("username", "")
        self.store.save_token(login, new)
        return new["access_token"]

    # -- Graph requests -----------------------------------------------------------

    def get(
        self,
        login: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        url = path if path.startswith("http") else f"{GRAPH_URL}{path}"
        refreshed = False
        need_refresh = False
        for attempt in range(4):
            token = self.access_token(login, force_refresh=need_refresh)
            need_refresh = False
            h = {"Authorization": f"Bearer {token}", **(headers or {})}
            r = self._http.get(url, params=params, headers=h)
            if r.status_code == 401 and not refreshed:
                refreshed = need_refresh = True
                continue
            if r.status_code in (429, 503, 504) and attempt < 3:
                try:
                    wait = float(r.headers.get("Retry-After", "2"))
                except ValueError:
                    wait = 2.0
                self._sleep(min(30.0, max(1.0, wait)))
                continue
            if r.status_code == 200:
                return r.json()
            raise M365Error(self._describe_error(r, "read mail"))
        raise M365Error("Microsoft Graph kept throttling the request; try again later.")

    # -- errors ---------------------------------------------------------------------

    @staticmethod
    def _safe_json(r: httpx.Response) -> Dict[str, Any]:
        try:
            d = r.json()
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _json(self, r: httpx.Response, what: str) -> Dict[str, Any]:
        if r.status_code != 200:
            raise M365Error(self._describe_error(r, what))
        return r.json()

    def _describe_error(self, r: httpx.Response, what: str) -> str:
        d = self._safe_json(r)
        if "error" in d and isinstance(d["error"], dict):  # Graph style
            code = d["error"].get("code", "")
            msg = d["error"].get("message", "")
        else:  # OAuth style
            code = d.get("error", "")
            msg = d.get("error_description", "") or r.text[:200]
        hint = ""
        text = f"{code} {msg}"
        if "AADSTS65001" in text or "consent" in text.lower():
            hint = (
                " Your organisation may require an admin to approve this app "
                "(admin consent)."
            )
        elif "AADSTS7000218" in text:
            hint = " In the app registration, enable 'Allow public client flows'."
        elif "AADSTS50020" in text or "AADSTS50194" in text:
            hint = (
                " The app registration must allow accounts from any organisation "
                "(multitenant) and personal accounts, or use the right --tenant."
            )
        elif r.status_code == 403:
            hint = " You don't have permission to read this mailbox."
        return f"Could not {what} (HTTP {r.status_code} {code}): {msg[:300]}{hint}"


# ---------------------------------------------------------------------------
# Mail operations
# ---------------------------------------------------------------------------


def _mailbox_base(account: Dict[str, Any]) -> str:
    mailbox = account.get("mailbox", "me")
    return "/me" if mailbox in ("", "me") else f"/users/{quote(mailbox)}"


def _addr(obj: Optional[Dict[str, Any]]) -> str:
    ea = (obj or {}).get("emailAddress", {}) or {}
    name, address = ea.get("name", ""), ea.get("address", "")
    if name and address and name != address:
        return f"{name} <{address}>"
    return address or name


def _parse_dt(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


def _search_term(q: str) -> str:
    q = re.sub(r'["\\]', " ", q or "").strip()
    if not q:
        raise M365Error("A search query is required.")
    return f'"{q[:200]}"'


class M365Mail:
    """High-level read-only mail access across all configured accounts."""

    def __init__(self, client: Optional[GraphClient] = None) -> None:
        self.client = client or GraphClient()
        self.store = self.client.store

    def account_names(self) -> List[str]:
        return sorted(self.store.accounts())

    def _targets(self, account: Optional[str]) -> List[str]:
        if account:
            a = validate_alias(account)
            self.store.get_account(a)
            return [a]
        names = self.account_names()
        if not names:
            raise M365Error(
                "No Microsoft 365 accounts yet. Run: jarvis m365 add <name>"
            )
        return names

    def _messages(
        self, alias: str, params: Dict[str, Any], headers=None
    ) -> List[Dict[str, Any]]:
        acct = self.store.get_account(alias)
        data = self.client.get(
            acct.get("login", alias), f"{_mailbox_base(acct)}/messages", params, headers
        )
        items = data.get("value", [])
        for it in items:
            it["_account"] = alias
        return items

    def recent(
        self, account: Optional[str] = None, *, top: int = 10, unread_only: bool = False
    ) -> List[Dict[str, Any]]:
        top = max(1, min(int(top), 50))
        out: List[Dict[str, Any]] = []
        for alias in self._targets(account):
            params: Dict[str, Any] = {
                "$top": top,
                "$select": _SELECT,
                "$orderby": "receivedDateTime desc",
            }
            if unread_only:
                params["$filter"] = "isRead eq false"
            out.extend(self._messages(alias, params))
        out.sort(key=lambda m: m.get("receivedDateTime", ""), reverse=True)
        return out[:top] if not account else out

    def search(
        self, query: str, account: Optional[str] = None, *, top: int = 10
    ) -> List[Dict[str, Any]]:
        top = max(1, min(int(top), 50))
        term = _search_term(query)
        out: List[Dict[str, Any]] = []
        for alias in self._targets(account):
            out.extend(
                self._messages(
                    alias, {"$search": term, "$top": top, "$select": _SELECT}
                )
            )
        out.sort(key=lambda m: m.get("receivedDateTime", ""), reverse=True)
        return out

    def read(self, account: str, message_id: str) -> Dict[str, Any]:
        alias = validate_alias(account)
        acct = self.store.get_account(alias)
        if not re.fullmatch(r"[A-Za-z0-9=_\-+/]{8,400}", message_id or ""):
            raise M365Error("That doesn't look like a message id.")
        data = self.client.get(
            acct.get("login", alias),
            f"{_mailbox_base(acct)}/messages/{quote(message_id, safe='')}",
            {"$select": _SELECT + ",body"},
            {"Prefer": 'outlook.body-content-type="text"'},
        )
        data["_account"] = alias
        return data

    # -- formatting --
    @staticmethod
    def format_list(items: List[Dict[str, Any]]) -> str:
        if not items:
            return "No messages found."
        lines = []
        for m in items:
            when = _parse_dt(m.get("receivedDateTime", "")).strftime("%Y-%m-%d %H:%M")
            flag = "" if m.get("isRead", True) else " [unread]"
            lines.append(
                f"[{m.get('_account', '')}] {when}{flag} | {_addr(m.get('from'))} | "
                f"{m.get('subject') or '(no subject)'}\n"
                f"    {(m.get('bodyPreview') or '').strip()[:200]}\n"
                f"    id={m.get('id', '')}"
            )
        return "\n".join(lines)

    @staticmethod
    def format_message(m: Dict[str, Any]) -> str:
        body = ((m.get("body") or {}).get("content") or "").strip()
        if len(body) > _MAX_BODY_CHARS:
            body = body[:_MAX_BODY_CHARS] + "\n…(truncated)"
        to = ", ".join(_addr(r) for r in m.get("toRecipients", []) or [])
        return (
            f"Account: {m.get('_account', '')}\n"
            f"From: {_addr(m.get('from'))}\nTo: {to}\n"
            f"Date: {m.get('receivedDateTime', '')}\n"
            f"Subject: {m.get('subject') or '(no subject)'}\n\n{body}"
        )


# ---------------------------------------------------------------------------
# Connector (for indexing into the knowledge store)
# ---------------------------------------------------------------------------


@ConnectorRegistry.register("m365")
class M365MailConnector(BaseConnector):
    """All configured Microsoft 365 / Outlook accounts, indexed read-only."""

    connector_id = "m365"
    display_name = "Microsoft 365 / Outlook (multi-account)"
    auth_type = "device_code"

    def __init__(
        self, *, max_messages: int = 300, mail: Optional[M365Mail] = None
    ) -> None:
        self._mail = mail
        self._max = max(1, int(max_messages))
        self._status = SyncStatus()

    @property
    def mail(self) -> M365Mail:
        if self._mail is None:
            self._mail = M365Mail()
        return self._mail

    def is_connected(self) -> bool:
        store = self.mail.store
        return any(
            store.load_token(a.get("login", n)) for n, a in store.accounts().items()
        )

    def disconnect(self) -> None:
        for alias in self.mail.account_names():
            self.mail.store.remove_account(alias)

    def sync(
        self, *, since: Optional[datetime] = None, cursor: Optional[str] = None
    ) -> Iterator[Document]:
        self._status = SyncStatus(state="syncing")
        total = 0
        for alias in self.mail.account_names():
            acct = self.mail.store.get_account(alias)
            params: Dict[str, Any] = {
                "$top": 50,
                "$select": _SELECT + ",body",
                "$orderby": "receivedDateTime desc",
            }
            if since is not None:
                ts = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                params["$filter"] = f"receivedDateTime ge {ts}"
            url: Optional[str] = f"{_mailbox_base(acct)}/messages"
            count = 0
            try:
                while url and count < self._max:
                    data = self.mail.client.get(
                        acct.get("login", alias),
                        url,
                        params if not url.startswith("http") else None,
                        {"Prefer": 'outlook.body-content-type="text"'},
                    )
                    for m in data.get("value", []):
                        if count >= self._max:
                            break
                        m["_account"] = alias
                        count += 1
                        yield self._to_document(alias, acct, m)
                    url = data.get("@odata.nextLink")
            except M365Error as exc:
                logger.warning("m365 sync for '%s' failed: %s", alias, exc)
                self._status.error = str(exc)
            total += count
        self._status.state = "idle"
        self._status.items_synced = total
        self._status.last_sync = datetime.now()

    @staticmethod
    def _to_document(alias: str, acct: Dict[str, Any], m: Dict[str, Any]) -> Document:
        participants = [_addr(m.get("from"))] + [
            _addr(r)
            for r in (m.get("toRecipients") or []) + (m.get("ccRecipients") or [])
        ]
        return Document(
            doc_id=f"m365:{alias}:{m.get('id', '')}",
            source="m365",
            doc_type="email",
            content=M365Mail.format_message(m),
            title=m.get("subject") or "(no subject)",
            author=_addr(m.get("from")),
            participants=[p for p in participants if p],
            timestamp=_parse_dt(m.get("receivedDateTime", "")),
            thread_id=m.get("conversationId"),
            url=m.get("webLink"),
            metadata={"account": alias, "mailbox": acct.get("address", "")},
            source_id=f"{alias}:{m.get('id', '')}",
            channel=alias,
        )

    def sync_status(self) -> SyncStatus:
        return self._status

    def mcp_tools(self) -> List[ToolSpec]:
        acct_param = {
            "type": "string",
            "description": (
                "Account name (see m365_accounts). Omit to use all accounts."
            ),
        }
        return [
            ToolSpec(
                name="m365_accounts",
                description="List the connected Microsoft 365 / Outlook mail accounts.",
                parameters={"type": "object", "properties": {}},
                category="email",
            ),
            ToolSpec(
                name="m365_mail_recent",
                description=(
                    "List recent emails from one or all Microsoft 365 / "
                    "Outlook accounts."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "account": acct_param,
                        "top": {
                            "type": "integer",
                            "description": "How many (1-50).",
                            "default": 10,
                        },
                        "unread_only": {"type": "boolean", "default": False},
                    },
                },
                category="email",
                timeout_seconds=60,
            ),
            ToolSpec(
                name="m365_mail_search",
                description=(
                    "Search emails (sender, subject, body) in one or all "
                    "Microsoft 365 / Outlook accounts."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "What to search for.",
                        },
                        "account": acct_param,
                        "top": {"type": "integer", "default": 10},
                    },
                    "required": ["query"],
                },
                category="email",
                timeout_seconds=60,
            ),
            ToolSpec(
                name="m365_mail_read",
                description=(
                    "Read the full text of one email by account and id "
                    "(ids come from recent/search)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "account": {"type": "string", "description": "Account name."},
                        "message_id": {"type": "string"},
                    },
                    "required": ["account", "message_id"],
                },
                category="email",
                timeout_seconds=60,
            ),
        ]


__all__ = [
    "GraphClient",
    "M365Error",
    "M365Mail",
    "M365MailConnector",
    "M365Store",
    "validate_alias",
]
