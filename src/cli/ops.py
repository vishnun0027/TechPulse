"""
techpulse-ops - Operator CLI
Uses the service-role Supabase key from .env (bypasses RLS).
Intended for server admins, cron jobs, and deployment pipelines.
"""

import asyncio
from typing import Any, Dict

import typer
from rich.console import Console
from rich.table import Table
from rich import print as rprint
from loguru import logger

# ── Top-level imports only (no import-inside-function anti-pattern) ───────────
from services.summarizer import main as summarizer_main
from services.enricher import embedder, deduplicator, novelty, clusterer
from services.ranker import scorer
from services.ranker.feedback_processor import process_feedback_batch
from services.ranker.hf_exporter import export_to_hf
from services.agents.research_agent import build_research_agent
from services.agents.composer_agent import compose_digest
from services.collector.main import collect
from services.delivery.main import deliver
from shared.redis_client import (
    read_from_group,
    acknowledge_message,
    ensure_group_exists,
)
from shared.config import settings
from shared.db import get_tenant_profiles, get_source_quality, get_filter_config

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


# ── Helpers ──────────────────────────────────────────────────────────────────


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


def _compute_topic_match(article_topics: list[str], user_allowed: list[str]) -> float:
    """
    Computes a real topic match ratio: intersection of article topics vs user interests.
    Returns a float 0.0–1.0. 0.5 is used as a neutral fallback if no topics are present.
    """
    if not article_topics or not user_allowed:
        return 0.5  # neutral fallback

    article_lower = {t.lower() for t in article_topics}
    allowed_lower = {t.lower() for t in user_allowed}
    matches = article_lower & allowed_lower
    # Jaccard-style: intersection over union
    union = article_lower | allowed_lower
    return round(len(matches) / len(union), 4) if union else 0.5


async def process_article_v2(
    db: Any,
    msg: Dict[str, Any],
    agent: Any,
    GROQ_API_KEY: str,
    semaphore: asyncio.Semaphore,
) -> bool:
    """
    Executes the full V2 pipeline for a single article message.
    Stages: 2 (Enrich) → 3 (Rank) → 4 (Research) → 5 (Save)
    """
    article = msg["data"]
    msg_id = msg["id"]
    user_id = article.get("user_id")
    title = article.get("title", "Untitled")

    async with semaphore:
        try:
            loop = asyncio.get_running_loop()
            content = article.get("content", "")

            # ── Stage 1.5: Content Quality Filter ────────────────────────────
            # Skip if content is too short (likely a repository link or placeholder)
            if len(content) < 200:
                rprint(f"[yellow]SKIP: Content too short ({len(content)} chars): {title[:50]}...[/yellow]")
                acknowledge_message(summarizer_main.GROUP_NAME, msg_id)
                return False

            # ── Stage 2: Enrich (Semantic) ────────────────────────────────────
            embedding = await loop.run_in_executor(
                None, embedder.embed_text, content or title, GROQ_API_KEY
            )

            if deduplicator.is_near_duplicate(db, embedding, user_id):
                rprint(f"[dim]SKIP: Near-duplicate: {title[:50]}...[/dim]")
                acknowledge_message(summarizer_main.GROUP_NAME, msg_id)
                return False

            novelty_score = novelty.compute_novelty_score(db, embedding, user_id)
            event_id = clusterer.find_or_create_event(
                db, summarizer_main.get_llm(), embedding, title, user_id
            )

            # ── Stage 3: Rank (with real signals) ────────────────────────────
            config = get_filter_config(user_id)
            quality = get_source_quality(article.get("source_id"), user_id)

            # Heuristic topic_match fallback
            article_topics = article.get("topics", [])
            allowed_topics = config.get("allowed", [])
            priority_topics = {t.lower() for t in config.get("priority", [])}

            if not article_topics and allowed_topics:
                text = (title + " " + article.get("content", "")).lower()
                matches = [t for t in allowed_topics if t.lower() in text]
                topic_match = 0.8 if matches else 0.4
                has_priority = any(t.lower() in priority_topics for t in matches)
            else:
                topic_match = _compute_topic_match(article_topics, allowed_topics)
                has_priority = any(t.lower() in priority_topics for t in article_topics)

            # Real base_relevance fallback
            db_score = article.get("score")
            base_relevance = float(db_score) if db_score is not None else 4.0

            signals = scorer.RankSignals(
                base_relevance=base_relevance,
                novelty_score=novelty_score,
                source_quality=quality,
                topic_match=topic_match,
                priority_boost=1.0 if has_priority else 0.0,
            )
            final_score = scorer.compute_final_score(signals)

            # ── Stage 3.5: Early Rejection ───────────────────
            if final_score < settings.delivery_threshold:
                rprint(
                    f"[dim]SKIP: Score too low ({final_score:.1f}) - dropping to save AI resources: {title[:40]}...[/dim]"
                )
                acknowledge_message(summarizer_main.GROUP_NAME, msg_id)
                return False

            # ── Stage 4: Research Agent (RAG Deep Dive) ───────────────────────
            result = await loop.run_in_executor(
                None,
                agent.invoke,
                {
                    "article_text": article.get("content", ""),
                    "article_title": title,
                    "user_id": user_id,
                    "embedding": embedding,
                },
            )

            # ── Stage 5: Save ─────────────────────────────────────────────────
            from shared.db import save_article

            save_success = save_article(
                {
                    "user_id": user_id,
                    "source_id": article.get("source_id"),
                    "title": title,
                    "source_url": article.get("source_url"),
                    "source": article.get("source"),
                    "content": article.get("content"),
                    "embedding": embedding,
                    "novelty_score": novelty_score,
                    "event_id": event_id,
                    "score": final_score,
                    "summary": result["summary"],
                    "why_it_matters": result["why_it_matters"],
                    "topics": result.get("topics", []),
                    "published_at": article.get("published_at") or None,
                    "v2_processed": True,
                }
            )

            if not save_success:
                logger.error(f"Failed to save processed article: {title}")
                return False

            acknowledge_message(summarizer_main.GROUP_NAME, msg_id)
            rprint(f"[green]Processed: {title[:50]}... [Score: {final_score}][/green]")
            return True

        except Exception as e:
            logger.exception(f"Failed to process V2 pipeline for '{title}': {e}")
            return False


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
def run_feedback_loop(
    days: int = typer.Option(7, help="Number of days of feedback to process"),
) -> None:
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


async def _run_all_async() -> None:
    """Internal async orchestrator for the full V2 pipeline."""
    console.rule("[bold cyan]TechPulse AI V2 Pipeline Orchestration")

    GROQ_API_KEY = settings.groq_api_key
    db = _get_db()
    loop = asyncio.get_running_loop()

    # ── Stage 1: Collect ──────────────────────────────────────────────────────
    ensure_group_exists(summarizer_main.GROUP_NAME)
    console.rule("[dim]Stage 1: Collect", align="left")
    await loop.run_in_executor(None, collect)

    # ── Stage 2-5: Enrich, Rank, Research ────────────────────────────────────
    console.rule("[dim]Stage 2-5: Personal Intelligence Enhancement", align="left")
    messages = read_from_group(
        summarizer_main.GROUP_NAME, summarizer_main.CONSUMER_NAME, count=50
    )

    if not messages:
        rprint("[yellow]No new articles to process.[/yellow]")
    else:
        rprint(
            f"[blue]Processing {len(messages)} articles concurrently (limit: {settings.max_concurrency})...[/blue]"
        )
        agent = build_research_agent(db, GROQ_API_KEY)
        semaphore = asyncio.Semaphore(settings.max_concurrency)

        tasks = [
            process_article_v2(db, msg, agent, GROQ_API_KEY, semaphore)
            for msg in messages
        ]
        results = await asyncio.gather(*tasks)

        success_count = sum(1 for r in results if r)
        rprint(
            f"[bold green]Enrichment batch complete: {success_count}/{len(messages)} articles processed.[/bold green]"
        )

    # ── Stage 6: Compose + Deliver ────────────────────────────────────────────
    console.rule("[dim]Stage 6: Multi-Channel Delivery", align="left")
    # Only deliver to non-admin active users
    delivery_targets = get_active_users(include_admins=False)

    for user_id in delivery_targets:
        digest = await loop.run_in_executor(
            None, compose_digest, db, summarizer_main.get_llm(), user_id
        )
        if not digest.get("empty"):
            await loop.run_in_executor(None, deliver, None, digest)
            rprint(f"[green]Digest delivered to user {user_id}[/green]")
        else:
            rprint(
                f"[yellow]No items above delivery threshold for user {user_id}[/yellow]"
            )

    rprint("\n[bold green]Full TechPulse V2 pipeline sequence complete.[/bold green]")


@run_app.command("all")
def run_all() -> None:
    """Execute the complete end-to-end V2 pipeline in parallel."""
    asyncio.run(_run_all_async())


# ── Monitor ──────────────────────────────────────────────────────────────────


@app.command("monitor")
def monitor(
    live: bool = typer.Option(
        True, "--live/--once", help="Enable auto-refreshing dashboard"
    ),
) -> None:
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
    res = (
        db.table("tenant_profiles")
        .select(
            "user_id, email, role, slack_webhook_url, discord_webhook_url, created_at"
        )
        .execute()
    )
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
            r["user_id"],
            r.get("email", "N/A"),
            r.get("role", "user"),
            "✓" if r.get("slack_webhook_url") else "-",
            "✓" if r.get("discord_webhook_url") else "-",
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
def reset(
    confirm: bool = typer.Option(
        False, "--confirm", help="Must be passed to verify destructive reset"
    ),
) -> None:
    """Danger: Wipe ALL data including articles, telemetry, and the Redis stream."""
    if not confirm:
        rprint(
            "[red] This will delete ALL data including article history. Pass --confirm to proceed.[/red]"
        )
        raise typer.Exit(1)

    from shared.maintenance import reset as do_reset

    asyncio.run(do_reset())
    rprint("[bold red]All system data wiped successfully.[/bold red]")


if __name__ == "__main__":
    app()
