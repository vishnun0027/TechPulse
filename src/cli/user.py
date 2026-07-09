"""
pulse - Unified TechPulse CLI
Authenticates via Supabase email/password. JWT stored in ~/.techpulse/config.json.
User-facing queries respect Row Level Security. Pipeline operations use service key.
"""

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt
from rich import print as rprint
from loguru import logger
from importlib.metadata import version as get_version

from cli.theme import PULSE_THEME
from cli.auth import get_user_client, _save_session, _clear_session
from cli.feeds import feeds_app, sources_list
from cli.filters import filter_app, topics_show

console = Console(theme=PULSE_THEME)

def version_callback(value: bool):
    if value:
        rprint(f"Pulse CLI [bold cyan]v{get_version('techpulse')}[/bold cyan]")
        raise typer.Exit()

app = typer.Typer(
    name="pulse",
    help=" [bold cyan]TechPulse[/bold cyan] — Your personal tech intelligence pipeline",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# ── Sub-command groups ────────────────────────────────────────────────────────

app.add_typer(feeds_app, name="feeds")
app.add_typer(filter_app, name="filter")

run_app = typer.Typer(help="Run pipeline services", no_args_is_help=True)
app.add_typer(run_app, name="run")

tenants_app = typer.Typer(help="Manage system tenants", no_args_is_help=True)
app.add_typer(tenants_app, name="tenants")


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

    # Legacy command deprecation warnings
    if ctx.invoked_subcommand == "sources":
        rprint("[yellow]Warning: 'sources' is deprecated. Use 'feeds' instead.[/yellow]")
    if ctx.invoked_subcommand == "topics":
        rprint("[yellow]Warning: 'topics' is deprecated. Use 'filter' instead.[/yellow]")


# ── Auth Commands ─────────────────────────────────────────────────────────────

@app.command("login")
def login() -> None:
    """Log in with your TechPulse account (email + password)."""
    from supabase import create_client
    from shared.config import settings

    url = settings.supabase_url
    anon_key = settings.supabase_anon_key

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
    """Show the currently authenticated user."""
    client, session = get_user_client()
    rprint(f"[bold cyan]{session['email']}[/bold cyan]  [dim](uid: {session['user_id']})[/dim]")


# ── Pipeline Status & Intelligence ────────────────────────────────────────────

@app.command("status")
def status() -> None:
    """Show your pipeline stats: articles scored, delivered, and pending."""
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
    """Read your latest AI-generated tech intelligence briefing."""
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


# ── Pipeline Run Commands (absorbed from ops CLI) ─────────────────────────────

def _check_redis_health() -> None:
    from shared.redis_client import ping_redis
    if not ping_redis():
        rprint("[bold red]ERROR: Redis server is unreachable. Please verify that your Redis service is running.[/bold red]")
        raise typer.Exit(code=1)

@run_app.command("collect")
def run_collect() -> None:
    """Scrape new articles from all active RSS sources into the processing queue."""
    _check_redis_health()
    from services.collector.main import collect
    console.rule("[bold blue]Collector Service")
    collect()
    rprint("[green]Collector finished.[/green]")

@run_app.command("summarize")
def run_summarize() -> None:
    """Analyze and summarize articles from the queue using AI."""
    _check_redis_health()
    from services.summarizer.main import summarize
    console.rule("[bold blue]Summarizer Service")
    asyncio.run(summarize())
    rprint("[green]Summarizer finished.[/green]")

@run_app.command("deliver")
def run_deliver() -> None:
    """Send digests to all configured webhooks (Slack/Discord)."""
    _check_redis_health()
    from services.delivery.main import deliver
    console.rule("[bold blue]Delivery Service")
    deliver()
    rprint("[green]Delivery finished.[/green]")

@run_app.command("all")
def run_all(limit: int = typer.Option(50, "--limit", "-l", help="Number of articles to process from queue")) -> None:
    """Execute the complete end-to-end pipeline (collect → enrich → deliver)."""
    _check_redis_health()
    from shared.db import supabase as db
    from cli.pipeline import run_all_async
    asyncio.run(run_all_async(db, limit=limit))

@run_app.command("feedback-loop")
def run_feedback_loop(days: int = typer.Option(7, help="Number of days of feedback to process")) -> None:
    """Process user feedback signals and update source quality scores."""
    from services.ranker.feedback_processor import process_feedback_batch
    console.rule("[bold blue]Feedback Loop Service")
    process_feedback_batch(days=days)
    rprint("[green]Feedback processing finished.[/green]")

@run_app.command("hf-export")
def run_hf_export(
    repo_id: str = typer.Option(..., help="Hugging Face repository ID (e.g. 'user/dataset')"),
    private: bool = typer.Option(True, help="Whether to keep the dataset private"),
) -> None:
    """Export processed intelligence data to a Hugging Face dataset."""
    from services.ranker.hf_exporter import export_to_hf
    console.rule("[bold blue]Hugging Face Export Service")
    export_to_hf(repo_id=repo_id, is_private=private)
    rprint("[green]Export finished.[/green]")


@run_app.command("purge")
def run_purge(
    days: int = typer.Option(90, help="Retention period in days"),
) -> None:
    """Purge system telemetry and old processed articles."""
    from shared.maintenance import purge_old_data
    console.rule("[bold blue]Data Purge & Retention Service")
    purge_old_data(days=days)
    rprint(f"[green]Data purge older than {days} days finished.[/green]")


# ── Tenant Management (absorbed from ops CLI) ─────────────────────────────────

@tenants_app.command("list")
def tenants_list() -> None:
    """List all registered system tenants and their configured webhook status."""
    from shared.db import supabase as db
    with console.status("Fetching tenants..."):
        try:
            res = db.table("tenant_profiles").select("user_id, email, role, slack_webhook_url, discord_webhook_url, created_at").execute()
            rows = res.data or []
        except Exception as e:
            rprint(f"[red]Error fetching tenants:[/red] {e}")
            raise typer.Exit(1)

    if not rows:
        rprint("[yellow]No tenants registered yet.[/yellow]")
        raise typer.Exit()

    table = Table(title="Registered TechPulse Tenants", show_lines=True)
    table.add_column("User ID", style="cyan", no_wrap=True)
    table.add_column("Email", style="bold")
    table.add_column("Role", style="magenta", justify="center")
    table.add_column("Slack", style="dim", justify="center")
    table.add_column("Discord", style="dim", justify="center")
    table.add_column("Created At", style="dim")

    for r in rows:
        table.add_row(
            r["user_id"], r.get("email", "N/A"), r.get("role", "user"),
            "✓" if r.get("slack_webhook_url") else "-", "✓" if r.get("discord_webhook_url") else "-",
            str(r.get("created_at", ""))[:19],
        )
    console.print(table)

@tenants_app.command("stats")
def tenants_stats() -> None:
    """View per-tenant usage statistics, including delivered and pending article counts."""
    from collections import defaultdict
    from shared.db import supabase as db
    with console.status("Fetching analytics..."):
        try:
            res = db.table("articles").select("user_id, is_delivered").execute()
            rows = res.data or []
        except Exception as e:
            rprint(f"[red]Error fetching analytics:[/red] {e}")
            raise typer.Exit(1)

    counts = defaultdict(lambda: {"total": 0, "delivered": 0})
    for r in rows:
        uid = r.get("user_id") or "Unknown"
        counts[uid]["total"] += 1
        if r.get("is_delivered"):
            counts[uid]["delivered"] += 1

    table = Table(title="Per-Tenant Article Analytics", show_lines=True)
    table.add_column("User ID", style="cyan")
    table.add_column("Total Scored", justify="right")
    table.add_column("Delivered", justify="right", style="green")
    table.add_column("Pending", justify="right", style="yellow")

    for uid, c in sorted(counts.items()):
        pending = c["total"] - c["delivered"]
        table.add_row(uid, str(c["total"]), str(c["delivered"]), str(pending))

    console.print(table)


# ── Monitoring ────────────────────────────────────────────────────────────────

@app.command("monitor")
def monitor(live: bool = typer.Option(True, "--live/--once", help="Enable auto-refreshing dashboard")) -> None:
    """Launch the live system monitor to track queue depth and telemetry."""
    import subprocess
    import sys
    args = [sys.executable, "-m", "shared.monitor"]
    if live:
        args.append("--live")
    subprocess.run(args)


# ── Maintenance ───────────────────────────────────────────────────────────────

@app.command("reset")
def reset(confirm: bool = typer.Option(False, "--confirm", help="Must be passed to verify destructive reset")) -> None:
    """Danger: Wipe ALL data including articles, telemetry, and the Redis stream."""
    if not confirm:
        rprint("[red]This will delete ALL data including article history. Pass --confirm to proceed.[/red]")
        raise typer.Exit(1)
    from shared.maintenance import reset as do_reset
    asyncio.run(do_reset())
    rprint("[bold red]All system data wiped successfully.[/bold red]")


@app.command("api")
def run_api_server(
    host: str = typer.Option("0.0.0.0", help="Host interface to bind to"),
    port: int = typer.Option(8000, help="Port to listen on"),
    reload: bool = typer.Option(True, "--reload/--no-reload", help="Enable auto-reload on code changes"),
) -> None:
    """Launch the FastAPI REST server for tech intelligence and management."""
    import uvicorn
    console.rule("[bold cyan]TechPulse API Server")
    rprint(f"[dim]Starting server on {host}:{port} (reload={reload})[/dim]")
    uvicorn.run("api.main:app", host=host, port=port, reload=reload, factory=False)


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
