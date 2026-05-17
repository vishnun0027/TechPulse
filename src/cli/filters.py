"""
techpulse - Filters Sub-CLI
Manages topic filters and priority settings.
"""
from typing import List

import typer
from rich.table import Table
from rich.console import Console
from rich import print as rprint

from cli.auth import get_user_client
from cli.theme import PULSE_THEME

console = Console(theme=PULSE_THEME)

filter_app = typer.Typer(help="Manage your topic filters", no_args_is_help=True)

@filter_app.command("show")
def topics_show() -> None:
    """Display your current personal topic filter and priority settings."""
    client, _ = get_user_client()
    res = client.table("app_config").select("value").eq("key", "topics").execute()

    if not res.data:
        rprint("[yellow]No topic config found. Run: techpulse topics set[/yellow]")
        raise typer.Exit()

    cfg = res.data[0]["value"]

    table = Table(title=" Your Personal Topic Filters", show_lines=False, box=None)
    table.add_column("Type", style="bold", width=20)
    table.add_column("Keywords", style="cyan")

    table.add_row("Allowed", ", ".join(cfg.get("allowed", [])) or "-")
    table.add_row("Blocked", ", ".join(cfg.get("blocked", [])) or "-")
    table.add_row("Priority", ", ".join(cfg.get("priority", [])) or "-")

    console.print(table)


@filter_app.command("set")
def topics_set(
    allowed: str = typer.Option("", "--allowed", help="Comma-separated topics you want to track"),
    blocked: str = typer.Option("", "--blocked", help="Comma-separated topics you want to ignore"),
    priority: str = typer.Option("", "--priority", help="Keywords that trigger a score boost"),
) -> None:
    """
    Update your personal topic filters and prioritization logic.
    """
    client, session = get_user_client()
    uid = session["user_id"]

    def clean(s: str) -> List[str]:
        return [t.strip() for t in s.split(",") if t.strip()]

    value = {
        "allowed": clean(allowed),
        "blocked": clean(blocked),
        "priority": clean(priority),
    }

    existing = client.table("app_config").select("key").eq("key", "topics").execute()
    if existing.data:
        client.table("app_config").update({"value": value}).eq("key", "topics").execute()
    else:
        client.table("app_config").insert({"key": "topics", "value": value, "user_id": uid}).execute()
