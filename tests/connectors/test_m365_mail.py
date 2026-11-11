"""[jarvis-fork] Microsoft 365 multi-account connector, against a fake Graph/login."""

import json
from urllib.parse import parse_qs

import httpx
import pytest

from openjarvis.connectors.m365_mail import (
    GraphClient,
    M365Error,
    M365Mail,
    M365MailConnector,
    M365Store,
    validate_alias,
)

CLIENT_ID = "11111111-2222-3333-4444-555555555555"


class FakeMS:
    """Minimal fake of login.microsoftonline.com + graph.microsoft.com."""

    def __init__(self):
        self.now = 1000.0
        self.pending_polls = 1
        self.requests = []
        self.mailboxes = {
            "tok-work": {
                "me": "me@work.com",
                "msgs": [self._m("w1", "Q3 plan", "2026-11-10T09:00:00Z", False)],
            },
            "tok-personal": {
                "me": "me@outlook.com",
                "msgs": [self._m("p1", "Dinner", "2026-11-11T08:00:00Z")],
            },
        }
        self.shared = {
            "support@work.com": [self._m("s1", "Ticket #9", "2026-11-09T07:00:00Z")]
        }
        self.refresh_count = 0
        self.throttle_once = False
        self.issue = "tok-work"

    @staticmethod
    def _m(mid, subj, when, read=True):
        return {
            "id": mid + "AAAAAAAA",
            "subject": subj,
            "receivedDateTime": when,
            "isRead": read,
            "from": {"emailAddress": {"name": "Boss", "address": "boss@work.com"}},
            "toRecipients": [{"emailAddress": {"address": "me@work.com"}}],
            "bodyPreview": f"preview {subj}",
            "webLink": "https://outlook/x",
            "conversationId": "c1",
            "body": {"content": f"Body of {subj}"},
        }

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        url = str(req.url)
        if req.url.host == "login.microsoftonline.com":
            form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
            assert form["client_id"] == CLIENT_ID
            if url.endswith("/devicecode"):
                assert (
                    "Mail.Read" in form["scope"] and "offline_access" in form["scope"]
                )
                return httpx.Response(
                    200,
                    json={
                        "device_code": "DC",
                        "user_code": "ABCD-EFGH",
                        "interval": 1,
                        "expires_in": 600,
                        "verification_uri": "https://microsoft.com/devicelogin",
                        "message": "go",
                    },
                )
            if form["grant_type"].endswith("device_code"):
                if self.pending_polls:
                    self.pending_polls -= 1
                    return httpx.Response(400, json={"error": "authorization_pending"})
                return httpx.Response(
                    200,
                    json={
                        "access_token": self.issue,
                        "refresh_token": "R1",
                        "expires_in": 3600,
                        "scope": "Mail.Read User.Read",
                    },
                )
            if form["grant_type"] == "refresh_token":
                self.refresh_count += 1
                if form["refresh_token"] == "REVOKED":
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(
                    200,
                    json={
                        "access_token": "tok-work",
                        "refresh_token": f"R{self.refresh_count + 1}",
                        "expires_in": 3600,
                    },
                )
        if req.url.host == "graph.microsoft.com":
            token = req.headers["Authorization"].split()[1]
            if self.throttle_once:
                self.throttle_once = False
                return httpx.Response(429, headers={"Retry-After": "1"}, json={})
            if token not in self.mailboxes:
                return httpx.Response(
                    401, json={"error": {"code": "InvalidAuthenticationToken"}}
                )
            path = req.url.path
            if path == "/v1.0/me":
                return httpx.Response(200, json={"mail": self.mailboxes[token]["me"]})
            if path.startswith("/v1.0/users/"):
                box = path.split("/")[3]
                if box not in self.shared:
                    return httpx.Response(
                        403,
                        json={"error": {"code": "ErrorAccessDenied", "message": "no"}},
                    )
                msgs = self.shared[box]
            else:
                msgs = self.mailboxes[token]["msgs"]
            if path.endswith("/messages"):
                return httpx.Response(200, json={"value": [dict(m) for m in msgs]})
            mid = path.rsplit("/", 1)[1]
            for m in msgs:
                if m["id"] == mid:
                    return httpx.Response(200, json=dict(m))
            return httpx.Response(404, json={"error": {"code": "ErrorItemNotFound"}})
        return httpx.Response(500)


@pytest.fixture
def fake(tmp_path):
    ms = FakeMS()
    store = M365Store(tmp_path / "m365")
    store.save_app(CLIENT_ID, "common")
    client = GraphClient(
        store,
        http=httpx.Client(transport=httpx.MockTransport(ms.handler)),
        clock=lambda: ms.now,
        sleep=lambda s: setattr(ms, "now", ms.now + s),
    )
    return ms, store, client, M365Mail(client)


def _add(ms, store, client, alias, token, tenant="common"):
    ms.issue = token
    ms.pending_polls = 1
    flow = client.start_device_flow(tenant)
    assert flow.user_code == "ABCD-EFGH"
    tok = client.poll_device_flow(flow)
    store.save_token(alias, tok)
    me = client.get(alias, "/me")
    store.save_account(
        alias,
        {"login": alias, "mailbox": "me", "address": me["mail"], "kind": "mailbox"},
    )


def test_multi_account_recent_and_search(fake):
    ms, store, client, mail = fake
    _add(ms, store, client, "work", "tok-work", "organizations")
    _add(ms, store, client, "personal", "tok-personal", "consumers")
    items = mail.recent(top=10)
    assert [i["_account"] for i in items] == [
        "personal",
        "work",
    ]  # newest first, merged
    assert "[unread]" in mail.format_list(items)
    only = mail.recent("work")
    assert {i["_account"] for i in only} == {"work"}
    res = mail.search('Q3 "plan"', "work")
    sent = [
        r
        for r in ms.requests
        if r.url.path.endswith("/messages") and "search" in str(r.url)
    ]
    assert sent and "%22Q3" in str(sent[-1].url)  # quoted, inner quotes stripped
    assert res[0]["subject"] == "Q3 plan"


def test_read_message_text_body(fake):
    ms, store, client, mail = fake
    _add(ms, store, client, "work", "tok-work")
    msg = mail.read("work", "w1AAAAAAAA")
    assert "Body of Q3 plan" in mail.format_message(msg)
    req = ms.requests[-1]
    assert req.headers["Prefer"] == 'outlook.body-content-type="text"'
    with pytest.raises(M365Error):
        mail.read("work", "../../etc")


def test_tokens_private_and_refresh_rotates(fake, tmp_path):
    ms, store, client, mail = fake
    _add(ms, store, client, "work", "tok-work")
    tok_file = tmp_path / "m365" / "tokens" / "work.json"
    assert (tok_file.stat().st_mode & 0o777) == 0o600
    assert ((tmp_path / "m365").stat().st_mode & 0o777) == 0o700
    ms.now += 4000  # expire
    mail.recent("work")
    assert ms.refresh_count == 1
    assert json.loads(tok_file.read_text())["refresh_token"] == "R2"


def test_revoked_refresh_gives_clear_message(fake):
    ms, store, client, mail = fake
    _add(ms, store, client, "work", "tok-work")
    tok = store.load_token("work")
    tok.update(refresh_token="REVOKED", expires_at=0)
    store.save_token("work", tok)
    with pytest.raises(M365Error, match="jarvis m365 add work"):
        mail.recent("work")


def test_throttling_retries(fake):
    ms, store, client, mail = fake
    _add(ms, store, client, "work", "tok-work")
    ms.throttle_once = True
    assert mail.recent("work")


def test_shared_mailbox_uses_users_path(fake):
    ms, store, client, mail = fake
    _add(ms, store, client, "work", "tok-work")
    store.save_account(
        "support",
        {
            "login": "work",
            "mailbox": "support@work.com",
            "address": "support@work.com",
            "kind": "shared",
        },
    )
    items = mail.recent("support")
    assert items[0]["subject"] == "Ticket #9"
    assert "/users/support%40work.com/messages" in str(
        ms.requests[-1].url
    ) or "/users/support@work.com/messages" in str(ms.requests[-1].url)
    store.save_account(
        "nope", {"login": "work", "mailbox": "nope@work.com", "kind": "shared"}
    )
    with pytest.raises(M365Error, match="permission"):
        mail.recent("nope")


def test_remove_keeps_shared_login_token(fake):
    ms, store, client, mail = fake
    _add(ms, store, client, "work", "tok-work")
    store.save_account(
        "support", {"login": "work", "mailbox": "support@work.com", "kind": "shared"}
    )
    assert store.remove_account("support")
    assert store.load_token("work")
    assert store.remove_account("work")
    assert store.load_token("work") == {}
    assert not store.remove_account("work")


def test_unknown_account_and_no_app(fake, tmp_path):
    ms, store, client, mail = fake
    with pytest.raises(M365Error, match="No Microsoft 365 accounts"):
        mail.recent()
    with pytest.raises(M365Error, match="No Microsoft 365 account named"):
        mail.recent("ghost")
    bare = GraphClient(M365Store(tmp_path / "other"), http=client._http)
    with pytest.raises(M365Error, match="jarvis m365 setup"):
        bare.start_device_flow("common")


def test_alias_validation():
    assert validate_alias("Work") == "work"
    for bad in ("", "a b", "../x", "x" * 40):
        with pytest.raises(M365Error):
            validate_alias(bad)
    with pytest.raises(M365Error):
        M365Store().save_app("not-a-guid")


def test_connector_sync_documents(fake):
    ms, store, client, mail = fake
    _add(ms, store, client, "work", "tok-work")
    _add(ms, store, client, "personal", "tok-personal")
    conn = M365MailConnector(mail=mail)
    assert conn.is_connected()
    docs = list(conn.sync())
    assert {d.doc_id for d in docs} == {
        "m365:personal:p1AAAAAAAA",
        "m365:work:w1AAAAAAAA",
    }
    d = next(x for x in docs if x.metadata["account"] == "work")
    assert (
        d.source == "m365" and d.doc_type == "email" and "Body of Q3 plan" in d.content
    )
    assert conn.sync_status().items_synced == 2
    assert {s.name for s in conn.mcp_tools()} == {
        "m365_accounts",
        "m365_mail_recent",
        "m365_mail_search",
        "m365_mail_read",
    }


def test_tools(fake):
    from openjarvis.tools.m365_mail import (
        M365AccountsTool,
        M365MailReadTool,
        M365MailRecentTool,
        M365MailSearchTool,
    )

    ms, store, client, mail = fake
    _add(ms, store, client, "work", "tok-work")
    acc = M365AccountsTool(mail=mail).execute()
    assert acc.success and "me@work.com" in acc.content
    rec = M365MailRecentTool(mail=mail).execute(top=5)
    assert rec.success and "Q3 plan" in rec.content
    rd = M365MailReadTool(mail=mail).execute(account="work", message_id="w1AAAAAAAA")
    assert rd.success and "Body of Q3 plan" in rd.content
    bad = M365MailSearchTool(mail=mail).execute(query="")
    assert not bad.success and "query" in bad.content
    spec = M365MailSearchTool().spec
    assert spec.parameters["required"] == ["query"]
    assert M365MailReadTool.is_local is False
