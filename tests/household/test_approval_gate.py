import threading
import time

from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.household.context import RequestContext, reset_request, set_request
from openjarvis.security.approval_gate import ApprovalGate, gate_tool_call, install_gate
from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec


def _gate(**kw):
    return ApprovalGate(
        auto_tools={"calculator", "web_search"}, timeout_seconds=5, **kw
    )


def _owner_ctx(sent):
    return RequestContext(
        person_id="owner",
        person_name="Shiven",
        role="owner",
        channel="slack",
        conversation_id="D1",
        notify=sent.append,
    )


def test_low_risk_runs_without_asking():
    g = _gate()
    sent = []
    tok = set_request(_owner_ctx(sent))
    try:
        assert g.check("calculator", {"expression": "1+1"}) is None
        assert g.check("http_request", {"url": "https://x", "method": "GET"}) is None
    finally:
        reset_request(tok)
    assert sent == []


def test_http_post_is_high_risk():
    g = _gate()
    assert g.classify_risk("http_request", {"method": "POST"}) == "high"
    assert g.classify_risk("http_request", {}) == "low"


def test_owner_approves_via_reply():
    g = _gate()
    sent = []
    result = {}

    def run():
        tok = set_request(_owner_ctx(sent))
        try:
            result["r"] = g.check("channel_send", {"to": "x", "text": "hi"})
        finally:
            reset_request(tok)

    t = threading.Thread(target=run)
    t.start()
    for _ in range(100):
        if g.pending("owner"):
            break
        time.sleep(0.01)
    pid = g.pending("owner")[0].id
    assert pid in sent[0] and "channel_send" in sent[0]
    assert g.handle_reply("owner", "hello there") is None  # not an approval reply
    assert g.handle_reply("mom", "yes") is None  # not theirs
    assert "Approved" in g.handle_reply("owner", f"yes {pid}")
    t.join(2)
    assert result["r"] is None
    assert g.history()[-1].status == "approved"


def test_plain_no_denies_single_pending():
    g = _gate()
    sent, result = [], {}

    def run():
        tok = set_request(_owner_ctx(sent))
        try:
            result["r"] = g.check("shell_exec", {"command": "rm -rf /tmp/x"})
        finally:
            reset_request(tok)

    t = threading.Thread(target=run)
    t.start()
    while not g.pending():
        time.sleep(0.01)
    assert "Cancelled" in g.handle_reply("owner", "no")
    t.join(2)
    assert "not approved" in result["r"]


def test_timeout_denies():
    g = ApprovalGate(auto_tools=set(), timeout_seconds=0)
    tok = set_request(_owner_ctx([]))
    try:
        msg = g.check("file_write", {"path": "/x"})
    finally:
        reset_request(tok)
    assert "timed out" in msg


def test_family_denied_high_risk_and_unlisted_tools():
    g = _gate()
    ctx = RequestContext(
        person_id="mom",
        person_name="Mom",
        role="family",
        extra={"allowed_tools": {"calculator", "shell_exec"}},
    )
    tok = set_request(ctx)
    try:
        assert g.check("calculator", {}) is None
        assert "not available" in g.check("m365_mail_read", {})
        assert "owner's approval" in g.check("shell_exec", {})
    finally:
        reset_request(tok)


def test_cloud_turn_blocks_sensitive_tools():
    g = ApprovalGate(auto_tools={"m365_mail_search"}, timeout_seconds=5)
    ctx = RequestContext(
        person_id="owner",
        person_name="S",
        role="owner",
        extra={"cloud_turn": True, "sensitive_tool": lambda n: n.startswith("m365_")},
    )
    tok = set_request(ctx)
    try:
        assert "local model" in g.check("m365_mail_search", {"query": "x"})
    finally:
        reset_request(tok)


def test_secrets_redacted_in_prompt():
    g = _gate()
    sent = []
    tok = set_request(_owner_ctx(sent))

    def answer():
        while not g.pending():
            time.sleep(0.01)
        g.resolve(g.pending()[0].id, False)

    threading.Thread(target=answer).start()
    try:
        g.check(
            "http_request",
            {"method": "POST", "headers": {"Authorization": "Bearer abc"}},
        )
    finally:
        reset_request(tok)
    assert "Bearer abc" not in sent[0] and "***" in sent[0]


def test_dashboard_queue_without_channel():
    g = _gate()
    result = {}
    t = threading.Thread(
        target=lambda: result.setdefault("r", g.check("file_write", {"p": 1}))
    )
    t.start()
    while not g.pending():
        time.sleep(0.01)
    p = g.pending()[0]
    assert p.channel == "api"
    assert g.resolve(p.id, True, by="dashboard")
    t.join(2)
    assert result["r"] is None


def test_no_gate_installed_is_noop():
    install_gate(None)
    assert gate_tool_call("shell_exec", {}) is None


class _Writer(BaseTool):
    tool_id = "file_write"

    def __init__(self, confirm=False):
        self.calls = 0
        self._confirm = confirm

    @property
    def spec(self):
        return ToolSpec(
            name=self.tool_id, description="w", requires_confirmation=self._confirm
        )

    def execute(self, **params):
        self.calls += 1
        return ToolResult(tool_name=self.tool_id, content="written", success=True)


def test_tool_executor_respects_gate_denial():
    install_gate(ApprovalGate(auto_tools=set(), timeout_seconds=0))
    try:
        tool = _Writer()
        res = ToolExecutor([tool]).execute(
            ToolCall(id="1", name="file_write", arguments="{}")
        )
        assert not res.success and "not approved" in res.content and tool.calls == 0
    finally:
        install_gate(None)


def test_requires_confirmation_routes_through_gate_when_active():
    g = ApprovalGate(auto_tools={"file_write"}, timeout_seconds=5)
    install_gate(g)
    try:
        tool = _Writer(confirm=True)
        ex = ToolExecutor([tool])
        out = {}
        t = threading.Thread(
            target=lambda: out.setdefault(
                "r", ex.execute(ToolCall(id="1", name="file_write", arguments="{}"))
            )
        )
        t.start()
        while not g.pending():
            time.sleep(0.01)
        g.resolve(g.pending()[0].id, True)
        t.join(2)
        assert out["r"].success and tool.calls == 1
    finally:
        install_gate(None)


def test_requires_confirmation_still_refused_without_gate():
    tool = _Writer(confirm=True)
    res = ToolExecutor([tool]).execute(
        ToolCall(id="1", name="file_write", arguments="{}")
    )
    assert not res.success and "confirmation" in res.content and tool.calls == 0
