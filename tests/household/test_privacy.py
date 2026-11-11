from openjarvis.household.privacy import PrivacyRouter
from tests.household.conftest import make_household


def _r(tmp_path):
    hh = make_household(tmp_path)
    return hh, PrivacyRouter(hh.config)


def test_private_keywords_stay_local(tmp_path):
    hh, r = _r(tmp_path)
    d = r.route(hh.config.owner, "Summarize the emails from my boss this week")
    assert d.model == "qwen3:8b" and d.private and not d.strip_sensitive_tools


def test_cloud_prefix_ignored_for_private(tmp_path):
    hh, r = _r(tmp_path)
    d = r.route(hh.config.owner, "/claude what's on my calendar tomorrow?")
    assert d.model == "qwen3:8b" and d.private
    assert d.query.startswith("what's on my calendar")


def test_sticky_privacy(tmp_path):
    hh, r = _r(tmp_path)
    d = r.route(
        hh.config.owner, "and what about the second one?", private_turns_remaining=3
    )
    assert d.private and d.model == "qwen3:8b"


def test_simple_goes_local_complex_goes_cloud(tmp_path):
    hh, r = _r(tmp_path)
    assert r.route(hh.config.owner, "hi").model == "qwen3:8b"
    complex_q = (
        "Compare and analyze the trade-offs between three approaches to building "
        "a distributed rate limiter. First explain token bucket, then sliding window, "
        "then evaluate which is best for multi-region deployments and justify why, "
        "step by step, with pros and cons for each?"
    )
    d = r.route(hh.config.owner, complex_q)
    assert d.model == "claude-sonnet-4-6"
    assert d.strip_sensitive_tools and d.drop_private_history and not d.private


def test_explicit_prefixes(tmp_path):
    hh, r = _r(tmp_path)
    assert r.route(hh.config.owner, "/gemini write a haiku").model == "gemini-2.5-pro"
    assert (
        r.route(hh.config.owner, "/claude write a haiku").model == "claude-sonnet-4-6"
    )
    assert (
        r.route(hh.config.owner, "/local write a long essay " * 20).model == "qwen3:8b"
    )


def test_family_routing(tmp_path):
    hh, r = _r(tmp_path)
    d = r.route(hh.config.get("mom"), "/claude check my email")
    assert d.model == "qwen3:8b" and d.strip_sensitive_tools and not d.private


def test_cloud_disabled(tmp_path):
    hh, r = _r(tmp_path)
    hh.config.models.cloud_enabled = False
    assert r.route(hh.config.owner, "/claude write an essay").model == "qwen3:8b"


def test_sensitive_tool_detection(tmp_path):
    _, r = _r(tmp_path)
    assert r.is_sensitive_tool("m365_mail_search")
    assert r.is_sensitive_tool("knowledge_search")
    assert not r.is_sensitive_tool("calculator")
    assert r.touched_private(["calculator", "gmail_search"])
