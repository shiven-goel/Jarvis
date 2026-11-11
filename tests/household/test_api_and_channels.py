from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.core.config import JarvisConfig
from openjarvis.household.api import router
from openjarvis.household.api_guard import apply_api_privacy
from openjarvis.household.context import current_request


def _msg(role, content, name=None):
    return SimpleNamespace(role=role, content=content, name=name)


def test_api_guard_forces_local_for_private(household):
    body = SimpleNamespace(
        model="claude-sonnet-4-6", messages=[_msg("user", "what's in my inbox?")]
    )
    assert apply_api_privacy(body)
    assert body.model == "qwen3:8b"
    ctx = current_request()
    assert ctx.is_owner and ctx.channel == "api" and ctx.extra["cloud_turn"] is False


def test_api_guard_respects_choice_for_non_private(household):
    body = SimpleNamespace(
        model="gemini-2.5-pro", messages=[_msg("user", "write a limerick")]
    )
    apply_api_privacy(body)
    assert body.model == "gemini-2.5-pro"
    assert current_request().extra["cloud_turn"] is True


def test_api_guard_sticky_from_tool_history(household):
    body = SimpleNamespace(
        model="claude-sonnet-4-6",
        messages=[
            _msg("user", "hello"),
            _msg("tool", "From: boss ...", name="m365_mail_search"),
            _msg("assistant", "Your boss wrote..."),
            _msg("user", "what did he want?"),
        ],
    )
    apply_api_privacy(body)
    assert body.model == "qwen3:8b"


def test_api_guard_prefix_strips_and_selects(household):
    m = _msg("user", "/gemini tell me a joke")
    body = SimpleNamespace(model="", messages=[m])
    apply_api_privacy(body)
    assert body.model == "gemini-2.5-pro" and m.content == "tell me a joke"


def test_api_guard_noop_without_household():
    body = SimpleNamespace(model="x", messages=[_msg("user", "my inbox")])
    assert apply_api_privacy(body) is None and body.model == "x"


def test_approvals_api(household):
    import threading
    import time

    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    out = {}
    t = threading.Thread(
        target=lambda: out.setdefault("r", household.gate.check("file_write", {}))
    )
    t.start()
    while not household.gate.pending():
        time.sleep(0.01)
    data = c.get("/v1/jarvis/approvals").json()
    pid = data["pending"][0]["id"]
    assert c.post(f"/v1/jarvis/approvals/{pid}/approve").json()["status"] == "approved"
    t.join(2)
    assert out["r"] is None
    assert c.post(f"/v1/jarvis/approvals/{pid}/deny").status_code == 404
    from tests.household.test_api_and_channels import _msg as _m

    apply_api_privacy(SimpleNamespace(model="", messages=[_m("user", "my inbox?")]))
    routes = c.get("/v1/jarvis/routes").json()["routes"]
    assert routes[0]["model"] == "qwen3:8b" and "inbox" not in str(routes)
    info = c.get("/v1/jarvis/household").json()
    assert info["models"]["local"] == "qwen3:8b"
    assert all("identities" not in p for p in info["people"])


def test_approvals_api_404_when_off():
    app = FastAPI()
    app.include_router(router)
    assert TestClient(app).get("/v1/jarvis/approvals").status_code == 404


def test_resolve_multiple_channels(monkeypatch):
    from openjarvis.system.builder import SystemBuilder

    cfg = JarvisConfig()
    cfg.channel.enabled = True
    cfg.channel.default_channel = "slack, whatsapp_baileys,,"
    seen = []

    def fake_resolve(self, config, bus, key=None):
        seen.append(key)
        return SimpleNamespace(channel_id=key)

    monkeypatch.setattr(SystemBuilder, "_resolve_channel", fake_resolve)
    chans = SystemBuilder(cfg)._resolve_channels(cfg, bus=None)
    assert [c.channel_id for c in chans] == ["slack", "whatsapp_baileys"]
    assert seen == ["slack", "whatsapp_baileys"]


def test_single_channel_resolution_unchanged():
    from openjarvis.system.builder import SystemBuilder

    cfg = JarvisConfig()
    cfg.channel.enabled = False
    assert SystemBuilder(cfg)._resolve_channels(cfg, bus=None) == []
    assert SystemBuilder(cfg)._resolve_channel(cfg, None) is None


def test_usage_api(household):
    app = FastAPI()
    app.include_router(router)
    household.model_router.record_usage(
        "claude-sonnet-4-6", {"prompt_tokens": 1000, "completion_tokens": 100}
    )
    data = TestClient(app).get("/v1/jarvis/usage").json()
    assert data["spent_today_usd"] > 0
    assert any(m["name"] == "qwen3:8b" and m["kind"] == "local" for m in data["models"])
