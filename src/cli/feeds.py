"""
techpulse - Feeds Sub-CLI
Manages RSS feeds and sources.
"""
from pathlib import Path
from typing import List, Dict, Set

import typer
from rich.table import Table
from rich.console import Console
from rich import print as rprint
from urllib.parse import urlparse

from cli.auth import get_user_client
from cli.theme import PULSE_THEME

console = Console(theme=PULSE_THEME)

feeds_app = typer.Typer(help="Manage your RSS feeds", no_args_is_help=True)

@feeds_app.command("list")
def sources_list() -> None:
    """List all your active RSS sources."""
    client, _ = get_user_client()
    res = client.table("rss_sources").select("*").order("name").execute()
    rows = res.data or []

    if not rows:
        rprint("[yellow]No sources configured. Run: techpulse feeds add NAME URL[/yellow]")
        raise typer.Exit()

    table = Table(title=" Your RSS Sources", show_lines=True)
    table.add_column("#", style="dim", justify="right", width=4)
    table.add_column("Name", style="bold cyan")
    table.add_column("URL", style="dim")
    table.add_column("Active", justify="center")

    for i, s in enumerate(rows, 1):
        table.add_row(str(i), s["name"], s["url"], "" if s.get("is_active", True) else "")

    console.print(table)

@feeds_app.command("add")
def sources_add(
    name: str = typer.Argument(..., help="Display name for this feed"),
    url: str = typer.Argument(..., help="Full URL of the RSS feed"),
) -> None:
    """Register a new RSS source to your pipeline."""
    client, session = get_user_client()
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
    client, _ = get_user_client()

    sources_res = client.table("rss_sources").select("id, name, url").execute()
    sources = {s["id"]: s for s in (sources_res.data or [])}

    if not sources:
        rprint("[yellow]No feeds found to analyze.[/yellow]")
        return

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

        if score >= 0.8:
            status, score_color = "[bold green]Trusted[/bold green]", "green"
        elif score >= 0.5:
            status, score_color = "[yellow]Stable[/yellow]", "yellow"
        else:
            status, score_color = "[red]Noisy[/red]", "red"

        table.add_row(
            source["name"],
            f"[{score_color}]{score:.2f}[/{score_color}]",
            str(delivered),
            status
        )

    console.print(table)

@feeds_app.command("remove")
def sources_remove(url: str = typer.Argument(..., help="URL of the source to remove")) -> None:
    """Remove an RSS source from your configuration by its URL."""
    client, _ = get_user_client()
    client.table("rss_sources").delete().eq("url", url).execute()
    rprint(f"[yellow]Removed:[/yellow] {url}")


def _parse_import_lines(lines: List[str], existing: Set[str], uid: str) -> tuple[List[Dict], List[str], List[str]]:
    """Helper to parse raw lines from an import file into DB rows."""
    rows, skipped, invalid = [], [], []
    for line in lines:
        name, url = "", ""
        if "|" in line:
            parts = line.split("|", 1)
            name, url = parts[0].strip(), parts[1].strip()
        elif line.startswith("http"):
            url = line.strip()
            try:
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

    return rows, skipped, invalid

@feeds_app.command("import")
def sources_import(file: Path = typer.Argument(..., help="Path to a text file")) -> None:
    """Bulk import multiple RSS sources from a formatted text file."""
    if not file.exists():
        rprint(f"[red]File not found: {file}[/red]")
        raise typer.Exit(1)

    client, session = get_user_client()
    uid = session["user_id"]

    existing_res = client.table("rss_sources").select("url").execute()
    existing = {r["url"].lower() for r in (existing_res.data or [])}

    lines = [
        line.strip()
        for line in file.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]

    rows, skipped, invalid = _parse_import_lines(lines, existing, uid)

    if not rows:
        rprint(f"[yellow]Nothing to import.[/yellow] Skipped: {len(skipped)}, Invalid: {len(invalid)}")
        raise typer.Exit()

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
