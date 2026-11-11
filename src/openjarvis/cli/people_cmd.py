"""``jarvis people`` -- check the household allowlist (people.toml).

[jarvis-fork]
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table


@click.group()
def people() -> None:
    """Who may talk to Jarvis (people.toml)."""


@people.command()
def check() -> None:
    """Validate people.toml and show who is allowed on which channel."""
    from openjarvis.household.config import default_people_path, load_household_config

    console = Console()
    path = default_people_path()
    try:
        cfg = load_household_config(path)
    except Exception as exc:
        console.print(f"[red]people.toml is invalid: {exc}[/red]")
        raise SystemExit(1)
    if cfg is None:
        console.print(
            f"[yellow]No {path} yet: the household layer is OFF and anyone "
            "who can reach a channel can talk to Jarvis.[/yellow]\n"
            "Copy personal/config/people.example.toml there."
        )
        raise SystemExit(1)
    t = Table("id", "name", "role", "slack", "whatsapp")
    for p in cfg.people:
        t.add_row(
            p.id,
            p.name,
            p.role,
            ", ".join(sorted(p.identities.get("slack", []))) or "-",
            ", ".join(sorted(p.identities.get("whatsapp", []))) or "-",
        )
    console.print(t)
    m = cfg.models
    console.print(
        f"Models: private/quick = [cyan]{m.local}[/cyan], "
        f"complex = [cyan]{m.cloud}[/cyan], "
        f"/gemini = [cyan]{m.cloud_alt}[/cyan], family = [cyan]{m.family}[/cyan]"
        + ("" if m.cloud_enabled else " [yellow](cloud disabled)[/yellow]")
    )
    console.print(
        f"Group chats ignored: {cfg.ignore_groups}. Approvals: "
        f"{'on' if cfg.approval.enabled else 'OFF'}, "
        f"timeout {cfg.approval.timeout_seconds // 60} min."
    )
    console.print(f"Family tools: {', '.join(sorted(cfg.family_tools))}")
    console.print("[green]people.toml OK[/green]")


__all__ = ["people"]
