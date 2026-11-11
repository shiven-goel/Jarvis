"""[jarvis-fork] Cost/token-aware model router."""

import json
from datetime import date

import pytest

from openjarvis.household.model_router import (
    ModelRouter,
    UsageTracker,
    classify_task,
    estimate_tokens,
    parse_router_config,
)

CATALOG = {
    "daily_budget_usd": 1.0,
    "models": [
        {
            "name": "qwen3:8b",
            "kind": "local",
            "tier": 1,
            "context_window": 8192,
            "aliases": ["local"],
        },
        {
            "name": "claude-haiku-5-5",
            "kind": "cloud",
            "tier": 2,
            "context_window": 1_000_000,
            "input_cost": 0.10,
            "output_cost": 0.50,
            "aliases": ["haiku"],
            "long_context_threshold": 100_000,
            "long_input_cost": 0.50,
            "long_output_cost": 2.50,
        },
        {
            "name": "claude-sonnet-5-5",
            "kind": "cloud",
            "tier": 3,
            "context_window": 1_000_000,
            "input_cost": 2.0,
            "output_cost": 10.0,
            "aliases": ["sonnet", "claude"],
        },
        {
            "name": "claude-opus-5-5",
            "kind": "cloud",
            "tier": 4,
            "context_window": 1_000_000,
            "input_cost": 4.0,
            "output_cost": 20.0,
            "aliases": ["opus"],
        },
    ],
}


def _router(tmp_path, raw=CATALOG, today=date(2026, 11, 11)):
    cfg = parse_router_config(raw, local="qwen3:8b", cloud="x", cloud_alt="y")
    return ModelRouter(cfg, UsageTracker(tmp_path / "u.json", today=lambda: today))


@pytest.mark.parametrize(
    "text,task",
    [
        ("hey!", "chat"),
        ("thanks", "chat"),
        ("what time zone is Tokyo in?", "quick"),
        ("Summarize this article for me", "summarize"),
        ("extract every date into JSON", "extract"),
        ("write a poem about rain", "writing"),
        ("why does my python function throw a KeyError", "coding"),
        ("```\nprint(1)\n```", "coding"),
        ("compare these two job offers", "analysis"),
        ("prove that sqrt(2) is irrational, step by step", "hard"),
        ("x " * 4000, "summarize"),
    ],
)
def test_classify(text, task):
    assert classify_task(text) == task


def test_estimate_tokens():
    assert estimate_tokens("a" * 400) == 104
    assert estimate_tokens("") == 4


def test_cheapest_capable_model(tmp_path):
    r = _router(tmp_path)
    assert r.choose("hello").model == "qwen3:8b"
    assert (
        r.choose("summarize the history of Rome in detail please").model
        == "claude-haiku-5-5"
    )
    assert (
        r.choose("write a short story about a dragon who loves tea").model
        == "claude-sonnet-5-5"
    )
    hard = r.choose(
        "design a distributed architecture and explain the trade-offs step by step"
    )
    assert hard.model == "claude-opus-5-5" and hard.task == "hard" and hard.est_cost > 0


def test_complexity_bumps_tier(tmp_path):
    r = _router(tmp_path)
    assert r.required_tier("quick", 0.6) == 2
    assert r.required_tier("quick", 0.05) == 1
    assert r.required_tier("summarize", 0.05) == 2  # summaries keep their tier
    assert r.required_tier("hard", 0.9) == 4  # capped


def test_token_size_moves_off_small_context(tmp_path):
    r = _router(tmp_path)
    small = r.choose("hi there")
    big = r.choose("hi there", history_tokens=20_000)
    assert small.model == "qwen3:8b"
    assert big.model == "claude-haiku-5-5" and "long input" in big.reason


def test_local_only_never_cloud_and_trims_when_too_big(tmp_path):
    r = _router(tmp_path)
    c = r.choose(
        "prove the theorem step by step", local_only=True, history_tokens=50_000
    )
    assert c.model == "qwen3:8b" and c.is_local
    assert "trimmed" in c.reason
    assert all(n == "qwen3:8b" or n.startswith("qwen") for n in c.fallbacks)


def test_forced_alias_and_ignored_when_local_only(tmp_path):
    r = _router(tmp_path)
    assert r.choose("tell me a joke", forced="opus").model == "claude-opus-5-5"
    c = r.choose("tell me a joke", forced="opus", local_only=True)
    assert c.model == "qwen3:8b" and "ignored" in c.reason


def test_preferred_model(tmp_path):
    raw = dict(CATALOG, prefer={"coding": "claude-opus-5-5"})
    assert (
        _router(tmp_path, raw).choose("debug this python code").model
        == "claude-opus-5-5"
    )


def test_long_context_pricing(tmp_path):
    r = _router(tmp_path)
    haiku = r.config.get("claude-haiku-5-5")
    assert haiku.cost(100_000, 0) == pytest.approx(0.01)
    assert haiku.cost(200_000, 0) == pytest.approx(0.10)


def test_budget_caps_then_blocks_cloud(tmp_path):
    r = _router(tmp_path)
    hard = "design an architecture and explain the trade-offs step by step"
    assert r.choose(hard).model == "claude-opus-5-5"
    r.usage.record("claude-opus-5-5", 0, 0, 0.85)  # 85% of $1
    c = r.choose(hard)
    assert c.model == "claude-sonnet-5-5" and "80%" in c.reason
    r.usage.record("claude-opus-5-5", 0, 0, 0.20)  # over budget
    c = r.choose(hard)
    assert c.model == "qwen3:8b" and "used up" in c.reason


def test_budget_resets_next_day(tmp_path):
    r = _router(tmp_path)
    r.usage.record("claude-opus-5-5", 0, 0, 5.0)
    r2 = _router(tmp_path, today=date(2026, 11, 12))
    assert r2.usage.spent_today() == 0
    assert (
        r2.choose("design an architecture, trade-offs step by step").model
        == "claude-opus-5-5"
    )


def test_usage_tracker_persists_privately(tmp_path):
    r = _router(tmp_path)
    cost = r.record_usage(
        "claude-sonnet-5-5", {"prompt_tokens": 1000, "completion_tokens": 500}
    )
    assert cost == pytest.approx(0.007)
    data = json.loads((tmp_path / "u.json").read_text())
    assert data["2026-11-11"]["claude-sonnet-5-5"]["requests"] == 1
    assert ((tmp_path / "u.json").stat().st_mode & 0o777) == 0o600
    r.record_usage("qwen3:8b", {"input_tokens": 10, "output_tokens": 5})
    assert r.usage.spent_today() == pytest.approx(0.007)


def test_fallbacks_ordered(tmp_path):
    c = _router(tmp_path).choose("write a story about a lighthouse keeper")
    assert c.model == "claude-sonnet-5-5"
    assert c.fallbacks[0] == "claude-opus-5-5"  # same-or-better tier first
    assert "qwen3:8b" in c.fallbacks


def test_minimal_catalog_without_router_section(tmp_path):
    cfg = parse_router_config(
        {}, local="qwen3:8b", cloud="claude-opus-5-5", cloud_alt="gemini-2.5-pro"
    )
    names = [m.name for m in cfg.models]
    assert names == ["qwen3:8b", "claude-opus-5-5", "gemini-2.5-pro"]
    assert cfg.by_alias("gemini").name == "gemini-2.5-pro"


@pytest.mark.parametrize(
    "raw,msg",
    [
        ({"models": [{"name": "a", "kind": "cloud"}]}, "local model"),
        ({"models": [{"name": "a", "kind": "local", "tier": 7}]}, "tier"),
        ({"models": [{"name": "a", "kind": "gpu"}]}, "kind"),
        ({"models": [{"kind": "local"}]}, "name"),
        ({"tasks": {"dance": 2}}, "unknown task"),
        ({"prefer": {"coding": "nope"}}, "unknown model"),
    ],
)
def test_config_validation(raw, msg):
    with pytest.raises(ValueError, match=msg):
        parse_router_config(raw, local="l", cloud="c", cloud_alt="")


def test_household_counts_usage_from_bus(tmp_path):
    from openjarvis.core.events import EventBus, EventType
    from tests.household.conftest import make_household

    hh = make_household(tmp_path)
    bus = EventBus()
    hh.attach_bus(bus)
    hh.attach_bus(bus)  # idempotent
    bus.publish(
        EventType.INFERENCE_END,
        {
            "model": "claude-sonnet-4-6",
            "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0},
        },
    )
    assert hh.usage.spent_today() == pytest.approx(
        3.0
    )  # default catalog price for "cloud"


def test_privacy_router_uses_catalog(tmp_path):
    from tests.household.conftest import PEOPLE_TOML, make_household

    text = (
        PEOPLE_TOML
        + "\n[router]\n"
        + "\n".join(
            "[[router.models]]\n"
            + "\n".join(f"{k} = {json.dumps(v)}" for k, v in m.items())
            for m in CATALOG["models"]
        )
    )
    hh = make_household(tmp_path, text)
    owner = hh.config.owner
    d = hh.router.route(owner, "/haiku summarize the plot of Hamlet")
    assert d.model == "claude-haiku-5-5" and not d.is_local and d.strip_sensitive_tools
    d = hh.router.route(owner, "/haiku summarize my emails")
    assert d.model == "qwen3:8b" and d.private
    assert hh.router.is_local_model("qwen3:8b") and not hh.router.is_local_model(
        "unknown"
    )


def test_wiring_falls_back_when_model_fails(tmp_path, household):
    from tests.household.test_wiring import FakeChannel, FakeSystem

    def reply(q, kw):
        if kw["model"] == "claude-sonnet-4-6":
            raise RuntimeError("overloaded")
        return {"content": f"answered by {kw['model']}", "tool_results": []}

    from openjarvis.household.wiring import wire_household_channel

    ch, sysm = FakeChannel(), FakeSystem(tmp_path, reply)
    wire_household_channel(sysm, ch, household)
    ch.deliver(
        channel="slack",
        sender="U0OWNER",
        content="/claude write me a poem",
        conversation_id="D1",
    )
    ch.wait_idle()
    models = [c.model for c in sysm.calls]
    assert models[0] == "claude-sonnet-4-6" and len(models) == 2
    assert ch.sent[-1][1] == f"answered by {models[1]}"
    assert any("fallback" in r["reason"] for r in household.recent_routes)


def test_private_turn_never_falls_back_to_cloud(tmp_path, household):
    from openjarvis.household.wiring import wire_household_channel
    from tests.household.test_wiring import FakeChannel, FakeSystem

    def reply(q, kw):
        raise RuntimeError("local model down")

    ch, sysm = FakeChannel(), FakeSystem(tmp_path, reply)
    wire_household_channel(sysm, ch, household)
    ch.deliver(
        channel="slack", sender="U0OWNER", content="read my inbox", conversation_id="D1"
    )
    ch.wait_idle()
    assert all(c.model == "qwen3:8b" for c in sysm.calls)
    assert "error" in ch.sent[-1][1]
