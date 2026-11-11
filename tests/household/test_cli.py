from click.testing import CliRunner

from openjarvis.cli.m365_cmd import m365
from openjarvis.cli.people_cmd import people
from tests.household.conftest import PEOPLE_TOML


def test_people_check(tmp_path, monkeypatch):
    p = tmp_path / "people.toml"
    monkeypatch.setenv("OPENJARVIS_PEOPLE", str(p))
    r = CliRunner().invoke(people, ["check"])
    assert r.exit_code == 1 and "OFF" in r.output
    p.write_text(PEOPLE_TOML)
    r = CliRunner().invoke(people, ["check"])
    assert r.exit_code == 0, r.output
    assert "people.toml OK" in r.output and "15550001111" in r.output
    p.write_text("[[people]]\nid='x'\nrole='family'\n")
    r = CliRunner().invoke(people, ["check"])
    assert r.exit_code == 1 and "invalid" in r.output


def test_m365_setup_and_list(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENJARVIS_HOME", str(tmp_path))
    r = CliRunner().invoke(m365, ["list"])
    assert "No accounts yet" in r.output
    r = CliRunner().invoke(m365, ["setup", "--client-id", "bad"])
    assert r.exit_code == 1
    r = CliRunner().invoke(
        m365, ["setup", "--client-id", "11111111-2222-3333-4444-555555555555"]
    )
    assert r.exit_code == 0 and "Saved" in r.output
    r = CliRunner().invoke(
        m365, ["add-shared", "s", "--via", "work", "--mailbox", "a@b.c"]
    )
    assert r.exit_code == 1 and "No Microsoft 365 account named 'work'" in r.output
