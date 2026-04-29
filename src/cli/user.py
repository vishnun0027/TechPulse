"""
techpulse - User CLI
Authenticates via Supabase email/password. JWT stored in ~/.techpulse/config.json.
All queries respect Row Level Security - data is scoped to the logged-in user.
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, List, Tuple

import typer
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt
from rich import print as rprint

app = typer.Typer(
    name="techpulse",
    help=" TechPulse AI - User CLI (your personal pipeline)",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

sources_app = typer.Typer(help="Manage your RSS sources", no_args_is_help=True)
app.add_typer(sources_app, name="sources")

topics_app = typer.Typer(help="Manage your topic filters", no_args_is_help=True)
app.add_typer(topics_app, name="topics")

console = Console()

CONFIG_PATH = Path.home() / ".techpulse" / "config.json"


# ── Auth Helpers ──────────────────────────────────────────────────────────────


def _load_session() -> Dict[str, Any]:
    """Loads the current user session from the local config file."""
    if not CONFIG_PATH.exists():
        rprint("[red]Not logged in. Run: techpulse login[/red]")
        raise typer.Exit(1)
    with open(CONFIG_PATH) as f:
        return json.load(f)


def _save_session(data: Dict[str, Any]) -> None:
    """Saves the user session details to the local config file."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(CONFIG_PATH, 0o600)  # Owner read/write only


def _clear_session() -> None:
    """Deletes the local session config file."""
    if CONFIG_PATH.exists():
        CONFIG_PATH.unlink()


def _get_user_client() -> Tuple[Any, Dict[str, Any]]:
    """
    Return a Supabase client authenticated as the current user.
    All operations will respect Row Level Security (RLS) on the database.

    Returns:
        Tuple[Client, Dict]: Authenticated client and the current session data.
    """
    from supabase import create_client

    session = _load_session()
    from shared.config import settings

    # Use project URL and the stored anon key + JWT
    client = create_client(settings.supabase_url, session["anon_key"])
    client.auth.set_session(session["access_token"], session["refresh_token"])
    return client, session


# ── Auth Commands ─────────────────────────────────────────────────────────────


@app.command("login")
def login() -> None:
    """Log in with your TechPulse account (email + password)."""
    from supabase import create_client
    from shared.config import settings

    # Determing the anon key for the Supabase instance
    anon_key = os.environ.get("SUPABASE_ANON_KEY", "")
    if not anon_key:
        anon_key = Prompt.ask(
            "Supabase Anon Key (from project settings)", password=True
        )

    email = Prompt.ask("Email")
    password = Prompt.ask("Password", password=True)

    with console.status("Authenticating..."):
        try:
            client = create_client(settings.supabase_url, anon_key)
            res = client.auth.sign_in_with_password(
                {"email": email, "password": password}
            )
        except Exception as e:
            rprint(f"[red]Login failed: {e}[/red]")
            raise typer.Exit(1)

    _save_session(
        {
            "access_token": res.session.access_token,
            "refresh_token": res.session.refresh_token,
            "user_id": res.user.id,
            "email": res.user.email,
            "anon_key": anon_key,
        }
    )
    rprint(f"[green]Logged in as [bold]{res.user.email}[/bold][/green]")


@app.command("logout")
def logout() -> None:
    """Log out and clear saved credentials."""
    _clear_session()
    rprint("[yellow]Logged out.[/yellow]")


@app.command("whoami")
def whoami() -> None:
    """Show the currently authenticated user session details."""
    client, session = _get_user_client()
    res = client.table("tenant_profiles").select("role").eq("user_id", session["user_id"]).execute()
    role = res.data[0].get("role", "user") if res.data else "user"
    
    rprint(
        f"[bold cyan]{session['email']}[/bold cyan]  [dim](uid: {session['user_id']})[/dim]  [magenta][{role}][/magenta]"
    )


# ── Pipeline Status ───────────────────────────────────────────────────────────


@app.command("status")
def status() -> None:
    """Show your personal pipeline stats: articles scored, delivered, and pending."""
    client, session = _get_user_client()

    total_res = client.table("articles").select("source_url", count="exact").execute()
    delivered = (
        client.table("articles")
        .select("source_url", count="exact")
        .eq("is_delivered", True)
        .execute()
    )
    pending = (
        client.table("articles")
        .select("source_url", count="exact")
        .eq("is_delivered", False)
        .gte("score", 2.5)
        .execute()
    )
    sources_res = client.table("rss_sources").select("id", count="exact").execute()

    table = Table(
        title=f"Pipeline Status: {session['email']}", show_lines=False, box=None
    )
    table.add_column("Metric", style="dim")
    table.add_column("Value", style="bold cyan", justify="right")

    table.add_row("RSS Sources", str(sources_res.count or 0))
    table.add_row("Total Articles Scored", str(total_res.count or 0))
    table.add_row("Delivered", str(delivered.count or 0))
    table.add_row("High-Score Pending", str(pending.count or 0))

    console.print(table)


# ── Sources Sub-Commands ──────────────────────────────────────────────────────


@sources_app.command("list")
def sources_list() -> None:
    """List all your active RSS sources."""
    client, _ = _get_user_client()
    res = client.table("rss_sources").select("*").order("name").execute()
    rows = res.data or []

    if not rows:
        rprint(
            "[yellow]No sources configured. Run: techpulse sources add NAME URL[/yellow]"
        )
        raise typer.Exit()

    table = Table(title=" Your RSS Sources", show_lines=True)
    table.add_column("#", style="dim", justify="right", width=4)
    table.add_column("Name", style="bold cyan")
    table.add_column("URL", style="dim")
    table.add_column("Active", justify="center")

    for i, s in enumerate(rows, 1):
        table.add_row(
            str(i), s["name"], s["url"], "" if s.get("is_active", True) else ""
        )

    console.print(table)


@sources_app.command("add")
def sources_add(
    name: str = typer.Argument(..., help="Display name for this feed"),
    url: str = typer.Argument(..., help="Full URL of the RSS feed"),
) -> None:
    """Register a new RSS source to your pipeline."""
    client, session = _get_user_client()
    res = (
        client.table("rss_sources")
        .insert({"name": name, "url": url, "user_id": session["user_id"]})
        .execute()
    )

    if res.data:
        rprint(f"[green]Added:[/green] {name} [dim]{url}[/dim]")
    else:
        rprint("[red]Failed to add source.[/red]")


@sources_app.command("remove")
def sources_remove(
    url: str = typer.Argument(..., help="URL of the source to remove"),
) -> None:
    """Remove an RSS source from your configuration by its URL."""
    client, _ = _get_user_client()
    client.table("rss_sources").delete().eq("url", url).execute()
    rprint(f"[yellow]Removed:[/yellow] {url}")


@sources_app.command("import")
def sources_import(
    file: Path = typer.Argument(
        ..., help="Path to a text file (Format: Name | URL per line)"
    ),
) -> None:
    """Bulk import multiple RSS sources from a formatted text file."""
    if not file.exists():
        rprint(f"[red]File not found: {file}[/red]")
        raise typer.Exit(1)

    client, session = _get_user_client()
    uid = session["user_id"]

    # Deduplicate against existing sources
    existing_res = client.table("rss_sources").select("url").execute()
    existing = {r["url"].lower() for r in (existing_res.data or [])}

    # Filter out comments and empty lines
    lines = [
        line.strip()
        for line in file.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    rows, skipped, invalid = [], [], []

    for line in lines:
        name, url = "", ""
        if "|" in line:
            parts = line.split("|", 1)
            name, url = parts[0].strip(), parts[1].strip()
        elif line.startswith("http"):
            url = line.strip()
            try:
                from urllib.parse import urlparse

                name = urlparse(url).hostname.removeprefix("www.")
            except Exception:
                name = url
        else:
            invalid.append(line)
            continue

        if url.lower() in existing:
            skipped.append(name)
            continue

        existing.add(url.lower())
        rows.append({"name": name, "url": url, "user_id": uid})

    if not rows:
        rprint(
            f"[yellow]Nothing to import.[/yellow] Skipped: {len(skipped)}, Invalid: {len(invalid)}"
        )
        raise typer.Exit()

    # Batch insert in chunks of 10
    inserted = 0
    for i in range(0, len(rows), 10):
        batch = rows[i : i + 10]
        res = client.table("rss_sources").insert(batch).execute()
        if res.data:
            inserted += len(res.data)

    parts = [f"[green]Imported {inserted} source(s)[/green]"]
    if skipped:
        parts.append(f"[dim]{len(skipped)} already existed[/dim]")
    if invalid:
        parts.append(f"[yellow]{len(invalid)} invalid line(s) skipped[/yellow]")
    rprint("  ".join(parts))


# ── Topics Sub-Commands ───────────────────────────────────────────────────────


@topics_app.command("show")
def topics_show() -> None:
    """Display your current personal topic filter and priority settings."""
    client, _ = _get_user_client()
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


@topics_app.command("set")
def topics_set(
    allowed: str = typer.Option(
        "", "--allowed", help="Comma-separated topics you want to track"
    ),
    blocked: str = typer.Option(
        "", "--blocked", help="Comma-separated topics you want to ignore"
    ),
    priority: str = typer.Option(
        "", "--priority", help="Keywords that trigger a score boost"
    ),
) -> None:
    """
    Update your personal topic filters and prioritization logic.

    Example:
      techpulse topics set --allowed "ai, llm" --blocked "crypto" --priority "open source"
    """
    client, session = _get_user_client()
    uid = session["user_id"]

    def clean(s: str) -> List[str]:
        return [t.strip() for t in s.split(",") if t.strip()]

    value = {
        "allowed": clean(allowed),
        "blocked": clean(blocked),
        "priority": clean(priority),
    }

    # Upsert logic for app_config
    existing = client.table("app_config").select("key").eq("key", "topics").execute()
    if existing.data:
        client.table("app_config").update({"value": value}).eq(
            "key", "topics"
        ).execute()
    else:
        client.table("app_config").insert(
            {"key": "topics", "value": value, "user_id": uid}
        ).execute()

    rprint("[green]Topic filters updated.[/green]")
    topics_show()


if __name__ == "__main__":
    app()
