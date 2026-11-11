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
    rt = Table("model", "kind", "tier", "context", "$/1M in", "$/1M out", "aliases")
    for mp in cfg.router.models:
        rt.add_row(
            mp.name + ("" if mp.enabled else " (off)"),
            mp.kind,
            str(mp.tier),
            f"{mp.context_window:,}",
            f"{mp.input_cost:g}",
            f"{mp.output_cost:g}",
            ", ".join("/" + a for a in mp.aliases) or "-",
        )
    console.print(rt)
    if cfg.router.daily_budget_usd:
        console.print(f"Daily cloud budget: ${cfg.router.daily_budget_usd:.2f}")
    console.print("[green]people.toml OK[/green]")


def _load_or_exit():
    from openjarvis.household import Household
    from openjarvis.household.config import load_household_config

    try:
        cfg = load_household_config()
    except Exception as exc:
        Console().print(f"[red]people.toml is invalid: {exc}[/red]")
        raise SystemExit(1)
    if cfg is None:
        Console().print(
            "[yellow]No people.toml yet (see: jarvis people check).[/yellow]"
        )
        raise SystemExit(1)
    return Household(cfg)


@people.command()
@click.argument("message")
@click.option(
    "--as", "as_person", default="", help="Person id to route as (default: owner)."
)
@click.option(
    "--history-tokens", default=0, help="Pretend the chat already has this many tokens."
)
def route(message: str, as_person: str, history_tokens: int) -> None:
    """Show which model would answer MESSAGE, and why (nothing is sent)."""
    hh = _load_or_exit()
    person = hh.config.get(as_person) if as_person else hh.config.owner
    if person is None:
        Console().print(f"[red]No person '{as_person}'.[/red]")
        raise SystemExit(1)
    d = hh.router.route(person, message, history_tokens=history_tokens)
    c = d.choice
    console = Console()
    console.print(
        f"Model:    [cyan]{d.model}[/cyan] ({'local' if d.is_local else 'cloud'})"
    )
    console.print(f"Why:      {d.reason}")
    if c is not None:
        console.print(f"Task:     {c.task} (needs tier {c.tier_needed})")
        console.print(f"Tokens:   ~{c.tokens_in} in / ~{c.tokens_out} out")
        console.print(f"Est cost: ${c.est_cost:.5f}")
        console.print(f"Fallback: {', '.join(c.fallbacks) or '-'}")
    console.print(
        f"Private:  {d.private}; personal-data tools "
        f"{'removed' if d.strip_sensitive_tools else 'allowed'}"
    )


@people.command()
def usage() -> None:
    """Show model-router spend for the last 7 days."""
    hh = _load_or_exit()
    console = Console()
    budget = hh.config.router.daily_budget_usd
    console.print(
        f"Today: ${hh.usage.spent_today():.4f}"
        + (f" of ${budget:.2f} budget" if budget else " (no daily budget set)")
    )
    t = Table("day", "model", "requests", "tokens in", "tokens out", "cost $")
    for day, models in hh.usage.summary(7).items():
        for name, u in sorted(models.items()):
            t.add_row(
                day,
                name,
                str(u["requests"]),
                str(u["input_tokens"]),
                str(u["output_tokens"]),
                f"{u['cost_usd']:.4f}",
            )
    console.print(t)


__all__ = ["people"]
