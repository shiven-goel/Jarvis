"""Shared fixtures for the [jarvis-fork] household layer tests."""

from __future__ import annotations

import textwrap

import pytest

from openjarvis.household import Household, reset_household, set_household
from openjarvis.household.config import parse_household

try:
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover
    import tomli as _toml  # type: ignore

PEOPLE_TOML = textwrap.dedent(
    """
    [settings]
    enabled = true
    ignore_groups = true

    [models]
    local = "qwen3:8b"
    cloud = "claude-sonnet-4-6"
    cloud_alt = "gemini-2.5-pro"
    family = "local"

    [approval]
    timeout_minutes = 1

    [[people]]
    id = "owner"
    name = "Shiven"
    role = "owner"
    slack = ["u0owner"]
    whatsapp = ["+1 (555) 000-1111"]

    [[people]]
    id = "mom"
    name = "Mom"
    role = "family"
    whatsapp = ["15550002222"]
    """
)


def make_household(tmp_path, text: str = PEOPLE_TOML) -> Household:
    data = _toml.loads(text)
    data.setdefault("settings", {})["data_dir"] = str(tmp_path / "people")
    return Household(parse_household(data), usage_path=tmp_path / "usage.json")


@pytest.fixture(autouse=True)
def _reset_household():
    reset_household()
    yield
    reset_household()


@pytest.fixture
def household(tmp_path):
    hh = make_household(tmp_path)
    set_household(hh)
    return hh
