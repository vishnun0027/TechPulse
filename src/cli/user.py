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
from loguru import logger
from importlib.metadata import version as get_version
from cli.theme import PULSE_THEME

def version_callback(value: bool):
    if value:
        rprint(f"Pulse CLI [bold cyan]v{get_version('techpulse-ai')}[/bold cyan]")
        raise typer.Exit()

app = typer.Typer(
    name="pulse",
    help=" [bold cyan]Pulse AI[/bold cyan] - Your personal tech intelligence pipeline",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# Sub-apps with new naming
feeds_app = typer.Typer(help="Manage your RSS feeds", no_args_is_help=True)
app.add_typer(feeds_app, name="feeds")

filter_app = typer.Typer(help="Manage your topic filters", no_args_is_help=True)
app.add_typer(filter_app, name="filter")

# Deprecation shim and global flags
@app.callback()
def main(
    ctx: typer.Context,
    version: bool = typer.Option(None, "--version", callback=version_callback, is_eager=True, help="Show version"),
    debug: bool = typer.Option(False, "--debug", help="Enable verbose logging to ~/.pulse/debug.log"),
):
    if debug:
        log_file = Path.home() / ".pulse" / "debug.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        logger.add(log_file, rotation="1 MB", level="DEBUG")
        logger.debug("Pulse CLI started in debug mode")

    if ctx.invoked_subcommand == "sources":
        rprint("[yellow]Warning: 'sources' is deprecated. Use 'feeds' instead.[/yellow]")
    if ctx.invoked_subcommand == "topics":
        rprint("[yellow]Warning: 'topics' is deprecated. Use 'filter' instead.[/yellow]")

console = Console(theme=PULSE_THEME)

import keyring

SERVICE_NAME = "techpulse-ai"
CONFIG_PATH = Path.home() / ".techpulse" / "config.json"


def _load_session() -> Dict[str, Any]:
    """Loads the user session from either System Keyring or local config fallback."""
    if not CONFIG_PATH.exists():
        rprint("[red]Not logged in. Run: pulse login[/red]")
        raise typer.Exit(1)
        
    with open(CONFIG_PATH) as f:
        session = json.load(f)
        
    # Attempt to pull sensitive tokens from OS Vault
    try:
        session["access_token"] = keyring.get_password(SERVICE_NAME, f"{session['user_id']}_access")
        session["refresh_token"] = keyring.get_password(SERVICE_NAME, f"{session['user_id']}_refresh")
    except Exception:
        # Keyring failed or unavailable (Headless Linux)
        pass

    if not session.get("access_token"):
        rprint("[red]Session credentials missing. Please login again.[/red]")
        raise typer.Exit(1)
        
    return session


def _save_session(data: Dict[str, Any]) -> None:
    """Saves tokens to Keyring with a transparent fallback to local JSON."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    uid = data["user_id"]
    
    # 1. Try to store sensitive tokens in OS Vault
    keyring_success = False
    try:
        keyring.set_password(SERVICE_NAME, f"{uid}_access", data["access_token"])
        keyring.set_password(SERVICE_NAME, f"{uid}_refresh", data["refresh_token"])
        keyring_success = True
    except Exception:
        # Headless mode: keys will be stored in the JSON instead
        pass
    
    # 2. Store meta in JSON (include tokens ONLY if keyring failed)
    meta = {
        "user_id": uid,
        "email": data["email"],
        "anon_key": data["anon_key"],
    }
    if not keyring_success:
        meta["access_token"] = data["access_token"]
        meta["refresh_token"] = data["refresh_token"]
        meta["vault_type"] = "file"
    else:
        meta["vault_type"] = "system"

    with open(CONFIG_PATH, "w") as f:
        json.dump(meta, f, indent=2)
    os.chmod(CONFIG_PATH, 0o600)


def _clear_session() -> None:
    """Deletes local config and clears tokens from Keyring if present."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                session = json.load(f)
                uid = session.get("user_id")
                if uid:
                    keyring.delete_password(SERVICE_NAME, f"{uid}_access")
                    keyring.delete_password(SERVICE_NAME, f"{uid}_refresh")
        except Exception:
            pass 
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
    try:
        # Attempt to set session. Supabase client will auto-refresh if possible.
        res = client.auth.set_session(session["access_token"], session["refresh_token"])
        
        # If the session was refreshed, update our local vault
        if res.session and res.session.access_token != session["access_token"]:
             _save_session({
                "access_token": res.session.access_token,
                "refresh_token": res.session.refresh_token,
                "user_id": session["user_id"],
                "email": session["email"],
                "anon_key": session["anon_key"],
            })
    except Exception:
        rprint("[bold red]Vault Access Error.[/bold red] Your session is no longer valid.")
        rprint("[dim]Please run: [white]pulse login[/white] to re-authenticate.[/dim]")
        _clear_session()
        raise typer.Exit(1)
        
    return client, session


# ── Onboarding & Auth Commands ────────────────────────────────────────────────


@app.command("init")
def init(
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Skip welcome screens")
) -> None:
    """
    Initialize your Pulse AI environment and link your account.
    """
    console.rule("[bold cyan]Welcome to Pulse AI[/bold cyan]")
    
    if not non_interactive:
        rprint("\n[dim]Pulse is your personal tech intelligence pipeline.\n"
               "This command will set up your local environment and link your account.[/dim]\n")

    # 1. Check if already logged in
    if CONFIG_PATH.exists():
        try:
            session = _load_session()
            rprint(f"[green]Pulse is already initialized for [bold]{session['email']}[/bold][/green]")
            if not typer.confirm("Do you want to re-initialize?"):
                return
        except Exception:
            rprint("[yellow]Existing configuration is corrupt. Re-initializing...[/yellow]")

    # 2. Run Login
    login()
    
    # 3. Bootstrap default config if missing
    client, session = _get_user_client()
    with console.status("Bootstrapping personal intelligence..."):
        existing = client.table("app_config").select("key").eq("key", "topics").execute()
        if not existing.data:
            default_topics = {"allowed": ["ai", "python", "rust"], "blocked": ["crypto"], "priority": []}
            client.table("app_config").insert({
                "key": "topics", 
                "value": default_topics, 
                "user_id": session["user_id"]
            }).execute()
            rprint("[dim]Initialized default topic filters (AI, Python, Rust).[/dim]")

    rprint("\n[bold green]✓ Initialization Complete![/bold green]")
    rprint("\n[bold]Next Steps:[/bold]")
    rprint("  1. Add your first feed: [cyan]pulse feeds add 'Hacker News' https://news.ycombinator.com/rss[/cyan]")
    rprint("  2. View your status:    [cyan]pulse status[/cyan]")
    rprint("  3. Read your digest:    [cyan]pulse digest[/cyan]\n")


@app.command("login")
def login() -> None:
    """Log in with your Pulse account (email + password)."""
    from supabase import create_client
    from shared.config import settings

    # 1. Determine Backend URL and Anon Key (Priority: Env -> Settings)
    url = "https://dhnujdduifibmalkyzhi.supabase.co"
    anon_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImRobnVqZGR1aWZpYm1hbGt5emhpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzY1MjQxMzMsImV4cCI6MjA5MjEwMDEzM30.vMAWYDZW76V2EgwlJugkF4d053CXWo4efI-yTpwhHVw"

    if not url:
        url = Prompt.ask("Supabase Project URL")
    if not anon_key:
        anon_key = Prompt.ask("Supabase Anon Key", password=True)

    console.print(f"[dim]Connecting to: {url}[/dim]")
    
    email = Prompt.ask("Email")
    password = Prompt.ask("Password", password=True)

    with console.status("Authenticating..."):
        try:
            client = create_client(url, anon_key)
            res = client.auth.sign_in_with_password({"email": email, "password": password})
            
            if not res.session:
                rprint("[red]Login failed: No session returned.[/red]")
                return

            _save_session({
                "access_token": res.session.access_token,
                "refresh_token": res.session.refresh_token,
                "user_id": res.user.id,
                "email": email,
                "anon_key": anon_key,
            })
            rprint(f"[bold green]Success![/bold green] Logged in as [bold]{email}[/bold]")
        except Exception as e:
            rprint(f"[red]Login failed:[/red] {str(e)}")


@app.command("logout")
def logout() -> None:
    """Log out and clear saved credentials."""
    _clear_session()
    rprint("[yellow]Logged out.[/yellow]")


@app.command("whoami")
def whoami() -> None:
    """Show the currently authenticated user session details."""
    client, session = _get_user_client()
    res = (
        client.table("tenant_profiles")
        .select("role")
        .eq("user_id", session["user_id"])
        .execute()
    )
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


# ── Feeds Sub-Commands (Legacy: sources) ──────────────────────────────────────
@feeds_app.command("list")
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


@feeds_app.command("add")
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


@feeds_app.command("health")
def feeds_health() -> None:
    """View the reliability and quality scores for all your RSS feeds."""
    client, _ = _get_user_client()
    
    # 1. Fetch names and URLs from rss_sources
    sources_res = client.table("rss_sources").select("id, name, url").execute()
    sources = {s["id"]: s for s in (sources_res.data or [])}
    
    if not sources:
        rprint("[yellow]No feeds found to analyze.[/yellow]")
        return

    # 2. Fetch telemetry from source_health
    health_res = client.table("source_health").select("source_id, quality_score, articles_delivered").execute()
    health_map = {h["source_id"]: h for h in (health_res.data or [])}

    table = Table(title=" RSS Source Health & Quality", show_lines=False, box=None)
    table.add_column("Source", style="bold cyan")
    table.add_column("Reliability", justify="right")
    table.add_column("Impact", justify="right")
    table.add_column("Status")

    for sid, source in sources.items():
        health = health_map.get(sid, {})
        score = health.get("quality_score", 0.5) or 0.5
        delivered = health.get("articles_delivered", 0) or 0
        
        # Color coding based on reliability
        if score >= 0.8:
            status = "[bold green]Trusted[/bold green]"
            score_color = "green"
        elif score >= 0.5:
            status = "[yellow]Stable[/yellow]"
            score_color = "yellow"
        else:
            status = "[red]Noisy[/red]"
            score_color = "red"

        table.add_row(
            source["name"],
            f"[{score_color}]{score:.2f}[/{score_color}]",
            str(delivered),
            status
        )

    console.print(table)


@feeds_app.command("remove")
def sources_remove(
    url: str = typer.Argument(..., help="URL of the source to remove"),
) -> None:
    """Remove an RSS source from your configuration by its URL."""
    client, _ = _get_user_client()
    client.table("rss_sources").delete().eq("url", url).execute()
    rprint(f"[yellow]Removed:[/yellow] {url}")


@feeds_app.command("import")
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


# ── Intelligence Commands ──────────────────────────────────────────────────────


@app.command("digest")
def pulse_digest(
    limit: int = typer.Option(10, "--limit", "-l", help="Number of articles to show"),
    min_score: float = typer.Option(3.0, "--min-score", "-s", help="Minimum score threshold"),
) -> None:
    """
    Fetch and read your latest AI-generated tech intelligence briefing.
    """
    from rich.markdown import Markdown
    client, session = _get_user_client()

    with console.status("Fetching latest intelligence..."):
        # Get high-scoring articles that are either delivered or pending
        res = (
            client.table("articles")
            .select("title, summary, why_it_matters, score, source, published_at")
            .gte("score", min_score)
            .order("published_at", desc=True)
            .limit(limit)
            .execute()
        )
        articles = res.data or []

    if not articles:
        rprint(f"[yellow]No high-scoring articles found (threshold: {min_score}).[/yellow]")
        return

    console.rule(f"[bold cyan]Intelligence Digest: {session['email']}")
    
    for i, art in enumerate(articles, 1):
        rprint(f"\n[bold cyan][{i}] {art['title']}[/bold cyan] [dim](Score: {art['score']:.1f} | {art['source']})[/dim]")
        
        md_content = f"**Summary**: {art['summary']}\n\n**Why it Matters**: {art['why_it_matters']}"
        console.print(Markdown(md_content))
        console.print("[dim]────────────────────────────────────────────────────────────────[/dim]")

    rprint(f"\n[bold green]✓ Showing {len(articles)} items above {min_score} threshold.[/bold green]")


# ── Filter Sub-Commands (Legacy: topics) ──────────────────────────────────────
@filter_app.command("show")
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


@filter_app.command("set")
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

# ── Legacy Aliases ────────────────────────────────────────────────────────────
@app.command("sources", hidden=True)
def sources_alias():
    rprint("[yellow]Warning: 'sources' is deprecated. Use 'feeds' instead.[/yellow]")
    sources_list()

@app.command("topics", hidden=True)
def topics_alias():
    rprint("[yellow]Warning: 'topics' is deprecated. Use 'filter' instead.[/yellow]")
    topics_show()

if __name__ == "__main__":
    app()
