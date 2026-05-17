"""
techpulse-ops - Operator CLI
Uses the service-role Supabase key from .env (bypasses RLS).
Intended for server admins, cron jobs, and deployment pipelines.
"""

import asyncio
from typing import Any

import typer
from rich.console import Console
from rich.table import Table
from rich import print as rprint

# ── Top-level imports only (no import-inside-function anti-pattern) ───────────
from services.collector.main import collect
from services.delivery.main import deliver
from services.ranker.feedback_processor import process_feedback_batch
from services.ranker.hf_exporter import export_to_hf
from shared.db import get_tenant_profiles
from cli.pipeline import process_article_v2, run_all_async

app = typer.Typer(
    name="techpulse-ops",
    help=" TechPulse AI - Operator CLI (system-level access)",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

run_app = typer.Typer(help="Run pipeline services", no_args_is_help=True)
app.add_typer(run_app, name="run")

tenants_app = typer.Typer(help="Manage tenants", no_args_is_help=True)
app.add_typer(tenants_app, name="tenants")

console = Console()

def _get_db() -> Any:
    """Returns the shared Supabase service-role client for administrative access."""
    from shared.db import supabase
    return supabase

def get_active_users(include_admins: bool = False) -> list[str]:
    """
    Fetches registered user IDs from tenant profiles.
    Admins are excluded by default from automated delivery.
    """
    profiles = get_tenant_profiles()
    if not include_admins:
        return [p["user_id"] for p in profiles if not p.get("is_admin")]
    return [p["user_id"] for p in profiles]

# ── Run Sub-Commands ─────────────────────────────────────────────────────────

@run_app.command("collect")
def run_collect() -> None:
    """Scrape new articles from all active multi-tenant RSS sources into the Redis queue."""
    console.rule("[bold blue]Collector Service")
    collect()
    rprint("[green]Collector finished.[/green]")

@run_app.command("summarize")
def run_summarize() -> None:
    """Analyze and summarize articles from the Redis queue using AI refinement."""
    console.rule("[bold blue]Summarizer Service (Legacy/Batch)")
    from services.summarizer.main import summarize
    asyncio.run(summarize())
    rprint("[green]Summarizer finished.[/green]")

@run_app.command("deliver")
def run_deliver() -> None:
    """Send personalized digests to all tenant webhooks (Slack/Discord)."""
    console.rule("[bold blue]Delivery Service")
    deliver()
    rprint("[green]Delivery finished.[/green]")

@run_app.command("feedback-loop")
def run_feedback_loop(days: int = typer.Option(7, help="Number of days of feedback to process")) -> None:
    """Process user feedback signals and update source quality scores."""
    console.rule("[bold blue]Feedback Loop Service")
    process_feedback_batch(days=days)
    rprint("[green]Feedback processing finished.[/green]")

@run_app.command("hf-export")
def run_hf_export(
    repo_id: str = typer.Option(..., help="Hugging Face repository ID (e.g. 'user/dataset')"),
    private: bool = typer.Option(True, help="Whether to keep the dataset private"),
) -> None:
    """Export processed intelligence data to a Hugging Face dataset."""
    console.rule("[bold blue]Hugging Face Export Service")
    export_to_hf(repo_id=repo_id, is_private=private)
    rprint("[green]Export finished.[/green]")

@run_app.command("all")
def run_all(limit: int = typer.Option(50, "--limit", "-l", help="Number of articles to process from queue")) -> None:
    """Execute the complete end-to-end V2 pipeline in parallel."""
    db = _get_db()
    asyncio.run(run_all_async(db, limit=limit))

# ── Monitor ──────────────────────────────────────────────────────────────────

@app.command("monitor")
def monitor(live: bool = typer.Option(True, "--live/--once", help="Enable auto-refreshing dashboard")) -> None:
    """Launch the live system monitor to track queue depth and telemetry stats."""
    import subprocess
    import sys
    args = [sys.executable, "-m", "shared.monitor"]
    if live:
        args.append("--live")
    subprocess.run(args)

# ── Tenants Sub-Commands ──────────────────────────────────────────────────────

@tenants_app.command("list")
def tenants_list() -> None:
    """List all registered system tenants and their configured webhook status."""
    db = _get_db()
    res = db.table("tenant_profiles").select("user_id, email, role, slack_webhook_url, discord_webhook_url, created_at").execute()
    rows = res.data or []

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
    db = _get_db()
    res = db.table("articles").select("user_id, is_delivered").execute()
    rows = res.data or []

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

# ── System Maintenance ────────────────────────────────────────────────────────

@app.command("reset")
def reset(confirm: bool = typer.Option(False, "--confirm", help="Must be passed to verify destructive reset")) -> None:
    """Danger: Wipe ALL data including articles, telemetry, and the Redis stream."""
    if not confirm:
        rprint("[red] This will delete ALL data including article history. Pass --confirm to proceed.[/red]")
        raise typer.Exit(1)
    from shared.maintenance import reset as do_reset
    asyncio.run(do_reset())
    rprint("[bold red]All system data wiped successfully.[/bold red]")

if __name__ == "__main__":
    app()
