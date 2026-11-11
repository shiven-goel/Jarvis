"""``jarvis m365`` -- manage Microsoft 365 / Outlook mail accounts (read-only).

[jarvis-fork]

    jarvis m365 setup --client-id <GUID>          once: your Entra app registration
    jarvis m365 add work                         sign in a work account
    jarvis m365 add personal --tenant consumers  sign in an Outlook.com account
    jarvis m365 add acme --tenant acme.com       another company's account
    jarvis m365 add-shared support --via work --mailbox support@company.com
    jarvis m365 list
    jarvis m365 test [name]
    jarvis m365 remove <name>
"""

from __future__ import annotations

import time

import click
from rich.console import Console
from rich.table import Table


def _mail():
    from openjarvis.connectors.m365_mail import M365Mail

    return M365Mail()


def _fail(exc: Exception) -> None:
    Console().print(f"[red]{exc}[/red]")
    raise SystemExit(1)


@click.group()
def m365() -> None:
    """Microsoft 365 / Outlook mail: add several accounts (read-only)."""


@m365.command()
@click.option(
    "--client-id", required=True, help="Application (client) ID of your Entra app."
)
@click.option(
    "--tenant",
    default="common",
    show_default=True,
    help="Default sign-in tenant: common, organizations, consumers, or a domain/GUID.",
)
def setup(client_id: str, tenant: str) -> None:
    """Save your Microsoft app registration (one time)."""
    from openjarvis.connectors.m365_mail import M365Error, M365Store

    try:
        M365Store().save_app(client_id, tenant)
    except M365Error as exc:
        _fail(exc)
    Console().print("[green]Saved.[/green] Next: [cyan]jarvis m365 add work[/cyan]")


@m365.command()
@click.argument("name")
@click.option(
    "--tenant",
    default="",
    help="Override tenant (consumers for Outlook.com, or a company domain).",
)
@click.option(
    "--shared-access",
    is_flag=True,
    help=(
        "Also request Mail.Read.Shared so shared mailboxes can be added via this login."
    ),
)
def add(name: str, tenant: str, shared_access: bool) -> None:
    """Sign in a mailbox and save it under NAME (e.g. work, personal)."""
    from openjarvis.connectors.m365_mail import M365Error, validate_alias

    console = Console()
    try:
        alias = validate_alias(name)
        mail = _mail()
        tenant = tenant or mail.store.load_app()["tenant"]
        flow = mail.client.start_device_flow(tenant, shared=shared_access)
        console.print(
            f"\n[bold cyan]1.[/bold cyan] Open [link]{flow.verification_uri}[/link] "
            f"on any device\n[bold cyan]2.[/bold cyan] Enter the code: "
            f"[bold yellow]{flow.user_code}[/bold yellow]\n"
            "[bold cyan]3.[/bold cyan] Sign in with the account you want to add "
            "and accept "
            "the read-only permissions.\n\nWaiting for you to finish…"
        )
        tok = mail.client.poll_device_flow(flow)
        mail.store.save_token(alias, tok)
        me = mail.client.get(
            alias, "/me", {"$select": "mail,userPrincipalName,displayName"}
        )
        address = me.get("mail") or me.get("userPrincipalName") or ""
        tok["username"] = address
        mail.store.save_token(alias, tok)
        mail.store.save_account(
            alias,
            {
                "login": alias,
                "mailbox": "me",
                "address": address,
                "kind": "mailbox",
                "tenant": tenant,
                "shared_access": shared_access,
                "added_at": int(time.time()),
            },
        )
    except M365Error as exc:
        _fail(exc)
    console.print(
        f"[green]Added '{alias}' ({address}).[/green] Try: jarvis m365 test {alias}"
    )


@m365.command("add-shared")
@click.argument("name")
@click.option(
    "--via", "via", required=True, help="Existing account whose sign-in has access."
)
@click.option(
    "--mailbox", required=True, help="Shared mailbox address, e.g. support@company.com"
)
def add_shared(name: str, via: str, mailbox: str) -> None:
    """Add a shared mailbox reachable through an existing sign-in."""
    from openjarvis.connectors.m365_mail import M365Error, validate_alias

    try:
        alias, login = validate_alias(name), validate_alias(via)
        mail = _mail()
        base = mail.store.get_account(login)
        if base.get("login", login) != login:
            raise M365Error(
                f"'{login}' is itself a shared mailbox; use the account it "
                "was added --via."
            )
        if not base.get("shared_access"):
            raise M365Error(
                f"Re-add '{login}' with --shared-access first: "
                f"jarvis m365 add {login} --shared-access"
            )
        if "@" not in mailbox:
            raise M365Error("--mailbox must be an email address.")
        entry = {
            "login": login,
            "mailbox": mailbox.strip(),
            "address": mailbox.strip(),
            "kind": "shared",
            "added_at": int(time.time()),
        }
        mail.store.save_account(alias, entry)
        try:
            mail.recent(alias, top=1)
        except M365Error:
            mail.store.remove_account(alias)
            raise
    except M365Error as exc:
        _fail(exc)
    Console().print(f"[green]Added shared mailbox '{alias}' ({mailbox}).[/green]")


@m365.command("list")
def list_accounts() -> None:
    """Show connected accounts."""
    mail = _mail()
    accts = mail.store.accounts()
    if not accts:
        Console().print("No accounts yet. Run: jarvis m365 add <name>")
        return
    t = Table("name", "address", "type", "signed in via")
    for alias, a in sorted(accts.items()):
        t.add_row(alias, a.get("address", ""), a.get("kind", ""), a.get("login", alias))
    Console().print(t)


@m365.command()
@click.argument("name", required=False)
def test(name: str | None) -> None:
    """Fetch the 3 newest emails to prove access works."""
    from openjarvis.connectors.m365_mail import M365Error

    try:
        mail = _mail()
        Console().print(mail.format_list(mail.recent(name, top=3)))
    except M365Error as exc:
        _fail(exc)


@m365.command()
@click.argument("name")
@click.confirmation_option(prompt="Remove this account and delete its saved sign-in?")
def remove(name: str) -> None:
    """Remove an account (and its tokens, if nothing else uses them)."""
    from openjarvis.connectors.m365_mail import validate_alias

    if _mail().store.remove_account(validate_alias(name)):
        Console().print(f"[green]Removed '{name}'.[/green]")
    else:
        _fail(Exception(f"No account named '{name}'."))


__all__ = ["m365"]
