"""
techpulse - User CLI
Authenticates via Supabase email/password. JWT stored in ~/.techpulse/config.json.
All queries respect Row Level Security - data is scoped to the logged-in user.
"""

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt
from rich import print as rprint
from loguru import logger
from importlib.metadata import version as get_version

from cli.theme import PULSE_THEME
from cli.auth import get_user_client, _load_session, _save_session, _clear_session, CONFIG_PATH
from cli.feeds import feeds_app, sources_list
from cli.filters import filter_app, topics_show

console = Console(theme=PULSE_THEME)

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

app.add_typer(feeds_app, name="feeds")
app.add_typer(filter_app, name="filter")

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

# ── Onboarding & Auth Commands ────────────────────────────────────────────────

@app.command("init")
def init(non_interactive: bool = typer.Option(False, "--non-interactive", help="Skip welcome screens")) -> None:
    """Initialize your Pulse AI environment and link your account."""
    console.rule("[bold cyan]Welcome to Pulse AI[/bold cyan]")

    if not non_interactive:
        rprint("\n[dim]Pulse is your personal tech intelligence pipeline.\n"
               "This command will set up your local environment and link your account.[/dim]\n")

    if CONFIG_PATH.exists():
        try:
            session = _load_session()
            rprint(f"[green]Pulse is already initialized for [bold]{session['email']}[/bold][/green]")
            if not typer.confirm("Do you want to re-initialize?"):
                return
        except Exception:
            rprint("[yellow]Existing configuration is corrupt. Re-initializing...[/yellow]")

    login()

    client, session = get_user_client()
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
    client, session = get_user_client()
    res = client.table("tenant_profiles").select("role").eq("user_id", session["user_id"]).execute()
    role = res.data[0].get("role", "user") if res.data else "user"

    rprint(f"[bold cyan]{session['email']}[/bold cyan]  [dim](uid: {session['user_id']})[/dim]  [magenta][{role}][/magenta]")

# ── Pipeline Status & Intelligence ────────────────────────────────────────────

@app.command("status")
def status() -> None:
    """Show your personal pipeline stats: articles scored, delivered, and pending."""
    client, session = get_user_client()

    total_res = client.table("articles").select("source_url", count="exact").execute()
    delivered = client.table("articles").select("source_url", count="exact").eq("is_delivered", True).execute()
    pending = client.table("articles").select("source_url", count="exact").eq("is_delivered", False).gte("score", 2.5).execute()
    sources_res = client.table("rss_sources").select("id", count="exact").execute()

    table = Table(title=f"Pipeline Status: {session['email']}", show_lines=False, box=None)
    table.add_column("Metric", style="dim")
    table.add_column("Value", style="bold cyan", justify="right")

    table.add_row("RSS Sources", str(sources_res.count or 0))
    table.add_row("Total Articles Scored", str(total_res.count or 0))
    table.add_row("Delivered", str(delivered.count or 0))
    table.add_row("High-Score Pending", str(pending.count or 0))

    console.print(table)

@app.command("digest")
def pulse_digest(
    limit: int = typer.Option(10, "--limit", "-l", help="Number of articles to show"),
    min_score: float = typer.Option(3.0, "--min-score", "-s", help="Minimum score threshold"),
) -> None:
    """Fetch and read your latest AI-generated tech intelligence briefing."""
    from rich.markdown import Markdown
    client, session = get_user_client()

    with console.status("Fetching latest intelligence..."):
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
