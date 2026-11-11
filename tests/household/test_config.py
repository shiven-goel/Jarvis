import pytest

from openjarvis.household.config import (
    is_group_conversation,
    load_household_config,
    normalize_identity,
    parse_household,
)
from tests.household.conftest import PEOPLE_TOML, make_household


def test_parse_people_and_roles(tmp_path):
    hh = make_household(tmp_path)
    cfg = hh.config
    assert cfg.owner.id == "owner"
    assert [p.role for p in cfg.people] == ["owner", "family"]
    assert cfg.approval.timeout_seconds == 60


@pytest.mark.parametrize(
    "channel,sender,expected",
    [
        ("whatsapp_baileys", "15550001111@s.whatsapp.net", "owner"),
        ("whatsapp_baileys", "15550001111:12@s.whatsapp.net", "owner"),
        ("whatsapp_baileys", "+1 555 000 2222", "mom"),
        ("slack", "U0OWNER", "owner"),
        ("slack", "u0owner", "owner"),
        ("slack", "U0STRANGER", None),
        ("whatsapp_baileys", "19998887777@s.whatsapp.net", None),
        ("telegram", "123", None),
        ("whatsapp_baileys", "", None),
    ],
)
def test_resolve_sender(tmp_path, channel, sender, expected):
    p = make_household(tmp_path).config.resolve_sender(channel, sender)
    assert (p.id if p else None) == expected


def test_whatsapp_lid_kept_distinct():
    assert normalize_identity("whatsapp", "4321@lid") == "4321@lid"
    assert normalize_identity("whatsapp", "4321@s.whatsapp.net") == "4321"


@pytest.mark.parametrize(
    "channel,cid,group",
    [
        ("whatsapp_baileys", "123-456@g.us", True),
        ("whatsapp_baileys", "status@broadcast", True),
        ("whatsapp_baileys", "15550001111@s.whatsapp.net", False),
        ("slack", "D012345", False),
        ("slack", "C012345", True),
        ("slack", "G012345", True),
    ],
)
def test_group_detection(channel, cid, group):
    assert is_group_conversation(channel, cid) is group


def test_requires_exactly_one_owner():
    with pytest.raises(ValueError, match="exactly one"):
        parse_household({"people": [{"id": "a", "role": "family"}]})
    with pytest.raises(ValueError, match="exactly one"):
        parse_household(
            {"people": [{"id": "a", "role": "owner"}, {"id": "b", "role": "owner"}]}
        )


def test_rejects_shared_identity_and_bad_role():
    with pytest.raises(ValueError, match="both"):
        parse_household(
            {
                "people": [
                    {"id": "a", "role": "owner", "slack": ["U1"]},
                    {"id": "b", "role": "family", "slack": ["u1"]},
                ]
            }
        )
    with pytest.raises(ValueError, match="role"):
        parse_household({"people": [{"id": "a", "role": "admin"}]})


def test_family_never_gets_sensitive_tools():
    cfg = parse_household(
        {
            "people": [{"id": "o", "role": "owner"}],
            "family": {"tools": ["calculator", "m365_mail_search", "knowledge_search"]},
        }
    )
    assert cfg.family_tools == frozenset({"calculator"})


def test_always_ask_overrides_auto():
    cfg = parse_household(
        {
            "people": [{"id": "o", "role": "owner"}],
            "approval": {"always_ask_tools": ["web_search"]},
        }
    )
    assert "web_search" not in cfg.approval.auto_tools


def test_missing_file_means_off(tmp_path):
    assert load_household_config(tmp_path / "nope.toml") is None


def test_load_from_file(tmp_path):
    p = tmp_path / "people.toml"
    p.write_text(PEOPLE_TOML)
    p.chmod(0o600)
    assert load_household_config(p).owner.name == "Shiven"
