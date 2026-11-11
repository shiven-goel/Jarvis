"""End-to-end tests of the household channel wiring with a fake channel/system."""

import threading
import time
from types import SimpleNamespace

import pytest

from openjarvis.channels._stubs import ChannelMessage
from openjarvis.core.config import JarvisConfig
from openjarvis.core.types import ToolResult
from openjarvis.household.context import current_request
from openjarvis.household.wiring import wire_household_channel
from openjarvis.security.approval_gate import gate_tool_call
from openjarvis.sessions.session import SessionStore
from openjarvis.tools._stubs import BaseTool, ToolSpec


class FakeChannel:
    channel_id = "fake"

    def __init__(self):
        self.handlers = []
        self.sent = []

    def on_message(self, h):
        self.handlers.append(h)

    def send(self, dest, text, conversation_id=""):
        self.sent.append((dest, text))
        return True

    def deliver(self, **kw):
        for h in self.handlers:
            h(ChannelMessage(**kw))

    def wait_idle(self):
        d = self._household_dispatcher
        d._pool.submit(lambda: None).result(5)
        # wait for queued work
        d._pool.shutdown(wait=True)


class _Tool(BaseTool):
    def __init__(self, name):
        self.tool_id = name

    @property
    def spec(self):
        return ToolSpec(name=self.tool_id, description=self.tool_id)

    def execute(self, **p):
        return ToolResult(tool_name=self.tool_id, content="ok", success=True)


class FakeSystem:
    def __init__(self, tmp_path, reply_fn=None):
        self.config = JarvisConfig()
        self.config.sessions.db_path = str(tmp_path / "sessions.db")
        self.session_store = SessionStore(db_path=tmp_path / "sessions.db")
        self.tools = [
            _Tool("calculator"),
            _Tool("m365_mail_search"),
            _Tool("shell_exec"),
        ]
        self.engine = object()
        self.agent_name = "orchestrator"
        self.calls = []
        self._reply_fn = reply_fn

    def ask(self, query, **kw):
        self.calls.append(SimpleNamespace(query=query, ctx=current_request(), **kw))
        if self._reply_fn:
            return self._reply_fn(query, kw)
        return {"content": f"echo: {query}", "tool_results": []}


@pytest.fixture
def wired(tmp_path, household):
    ch, sysm = FakeChannel(), FakeSystem(tmp_path)
    wire_household_channel(sysm, ch, household)
    return ch, sysm, household


OWNER_WA = "15550001111@s.whatsapp.net"


def test_stranger_and_group_ignored(wired):
    ch, sysm, _ = wired
    ch.deliver(
        channel="whatsapp_baileys",
        sender="19990000000@s.whatsapp.net",
        content="hi",
        conversation_id="19990000000@s.whatsapp.net",
    )
    ch.deliver(
        channel="whatsapp_baileys",
        sender=OWNER_WA,
        content="hi",
        conversation_id="1234-5678@g.us",
    )
    ch.deliver(channel="slack", sender="U0OWNER", content="hi", conversation_id="C123")
    ch.wait_idle()
    assert sysm.calls == [] and ch.sent == []


def test_stranger_reply_when_configured(tmp_path, household):
    household.config.stranger_reply = "Sorry, I only talk to my family."
    ch, sysm = FakeChannel(), FakeSystem(tmp_path)
    wire_household_channel(sysm, ch, household)
    ch.deliver(channel="slack", sender="U9", content="hi", conversation_id="D9")
    ch.wait_idle()
    assert ch.sent == [("D9", "Sorry, I only talk to my family.")]


def test_owner_private_message_runs_local_with_all_tools(wired):
    ch, sysm, _ = wired
    ch.deliver(
        channel="whatsapp_baileys",
        sender=OWNER_WA,
        content="check my inbox",
        conversation_id=OWNER_WA,
        message_id="m1",
    )
    ch.wait_idle()
    call = sysm.calls[0]
    assert call.model == "qwen3:8b"
    assert {t.spec.name for t in call.tool_instances} == {
        "calculator",
        "m365_mail_search",
        "shell_exec",
    }
    assert call.ctx.is_owner and call.ctx.extra["cloud_turn"] is False
    assert call.context is True
    assert ch.sent[-1] == (OWNER_WA, "echo: check my inbox")
    msgs = sysm.session_store.get_or_create(
        f"owner:whatsapp_baileys:{OWNER_WA}"
    ).messages
    assert msgs[0].metadata["private"] is True


def test_cloud_turn_strips_tools_and_private_history(tmp_path, household):
    ch, sysm = FakeChannel(), FakeSystem(tmp_path)
    wire_household_channel(sysm, ch, household)
    # 1) private turn
    ch.deliver(
        channel="slack",
        sender="U0OWNER",
        content="summarize my email",
        conversation_id="D1",
    )
    # 2..7) enough non-private turns to expire stickiness
    for i in range(6):
        ch.deliver(
            channel="slack",
            sender="U0OWNER",
            content=f"/local hello {i}",
            conversation_id="D1",
        )
    # 8) cloud turn
    ch.deliver(
        channel="slack",
        sender="U0OWNER",
        content="/claude write a poem",
        conversation_id="D1",
    )
    ch.wait_idle()
    first, last = sysm.calls[1], sysm.calls[-1]
    assert first.model == "qwen3:8b"  # sticky after the private turn
    assert last.model == "claude-sonnet-4-6"
    assert {t.spec.name for t in last.tool_instances} == {"calculator", "shell_exec"}
    assert all("summarize my email" not in m.content for m in last.prior_messages)
    assert last.ctx.extra["cloud_turn"] is True


def test_private_tool_use_makes_turn_private(tmp_path, household):
    def reply(q, kw):
        return {"content": "done", "tool_results": [{"tool_name": "m365_mail_search"}]}

    ch, sysm = FakeChannel(), FakeSystem(tmp_path, reply)
    wire_household_channel(sysm, ch, household)
    ch.deliver(channel="slack", sender="U0OWNER", content="hello", conversation_id="D1")
    ch.deliver(
        channel="slack",
        sender="U0OWNER",
        content="/claude and then?",
        conversation_id="D1",
    )
    ch.wait_idle()
    assert sysm.calls[1].model == "qwen3:8b"  # sticky: last turn touched mail


def test_family_gets_own_memory_and_safe_tools(wired, tmp_path):
    ch, sysm, hh = wired
    ch.deliver(
        channel="whatsapp_baileys",
        sender="15550002222@s.whatsapp.net",
        content="/claude read Shiven's email",
        conversation_id="15550002222@s.whatsapp.net",
    )
    ch.wait_idle()
    call = sysm.calls[0]
    names = {t.spec.name for t in call.tool_instances}
    assert "m365_mail_search" not in names and "shell_exec" not in names
    assert names <= set(hh.config.family_tools)
    assert call.model == "qwen3:8b"
    assert call.context is False
    mom_dir = hh.config.data_dir / "mom"
    assert call.memory_files.user_path == str(mom_dir / "USER.md")
    assert call.memory_files.memory_path == str(mom_dir / "MEMORY.md")
    assert call.memory_backend._db_path == str(mom_dir / "memory.db")
    assert (
        "Mom" in call.system_prompt_config.prefix
        and "no access" in call.system_prompt_config.prefix
    )
    # memory tools are bound to Mom's DB, not the owner's
    for t in call.tool_instances:
        if t.spec.name.startswith("memory_") and hasattr(t, "_backend"):
            assert t._backend is call.memory_backend
    assert (mom_dir.stat().st_mode & 0o777) == 0o700


def test_approval_flow_through_channel(tmp_path, household):
    def reply(q, kw):
        denial = gate_tool_call("shell_exec", {"command": "ls"})
        return {
            "content": "ran" if denial is None else f"blocked: {denial}",
            "tool_results": [],
        }

    ch, sysm = FakeChannel(), FakeSystem(tmp_path, reply)
    wire_household_channel(sysm, ch, household)
    ch.deliver(
        channel="slack", sender="U0OWNER", content="list files", conversation_id="D1"
    )
    for _ in range(200):
        if household.gate.pending("owner"):
            break
        time.sleep(0.01)
    pid = household.gate.pending("owner")[0].id
    assert any(pid in text for _, text in ch.sent)
    # The reply arrives while the turn is waiting; it must not deadlock.
    ch.deliver(
        channel="slack", sender="U0OWNER", content=f"yes {pid}", conversation_id="D1"
    )
    ch.wait_idle()
    texts = [t for _, t in ch.sent]
    assert any("Approved" in t for t in texts)
    assert texts[-1] == "ran"
    assert len(sysm.calls) == 1  # the "yes" was not sent to the agent


def test_family_cannot_approve_owner_actions(tmp_path, household):
    def reply(q, kw):
        return {"content": str(gate_tool_call("shell_exec", {})), "tool_results": []}

    ch, sysm = FakeChannel(), FakeSystem(tmp_path, reply)
    wire_household_channel(sysm, ch, household)
    ch.deliver(
        channel="whatsapp_baileys",
        sender="15550002222@s.whatsapp.net",
        content="run ls",
        conversation_id="15550002222@s.whatsapp.net",
    )
    ch.wait_idle()
    assert "not available" in ch.sent[-1][1] or "owner" in ch.sent[-1][1]


def test_messages_processed_in_order_per_conversation(tmp_path, household):
    order = []
    lock = threading.Lock()

    def reply(q, kw):
        time.sleep(0.02 if q.endswith("0") else 0)
        with lock:
            order.append(q)
        return {"content": q, "tool_results": []}

    ch, sysm = FakeChannel(), FakeSystem(tmp_path, reply)
    wire_household_channel(sysm, ch, household)
    for i in range(5):
        ch.deliver(
            channel="slack",
            sender="U0OWNER",
            content=f"/local m{i}",
            conversation_id="D1",
        )
    ch.wait_idle()
    assert order == [f"m{i}" for i in range(5)]


def test_system_wire_channel_delegates_when_household(tmp_path, household, monkeypatch):
    from openjarvis.system.core import JarvisSystem

    seen = {}
    monkeypatch.setattr(
        "openjarvis.household.wiring.wire_household_channel",
        lambda s, c, h: seen.setdefault("hh", h),
    )
    fake = SimpleNamespace(session_store=None, config=JarvisConfig())
    JarvisSystem.wire_channel(fake, FakeChannel())
    assert seen["hh"] is household
