"""
techpulse - Pipeline logic
Contains the orchestrator and processing steps for the V2 agentic pipeline.
"""
import asyncio
from typing import Any, Dict, List

from loguru import logger
from rich import print as rprint

from services.summarizer import main as summarizer_main
from services.enricher import embedder, deduplicator, novelty, clusterer
from services.ranker import scorer
from services.agents.research_agent import build_research_agent
from services.agents.composer_agent import compose_digest
from services.collector.main import collect
from services.delivery.main import deliver
from shared.redis_client import read_from_group, acknowledge_message, ensure_group_exists
from shared.config import settings
from shared.db import save_article, log_rejection, get_source_quality, get_filter_config
from shared.utils import clean_html


def _compute_topic_match(article_topics: list[str], user_allowed: list[str]) -> float:
    if not article_topics or not user_allowed:
        return 0.5
    article_lower = {t.lower() for t in article_topics}
    allowed_lower = {t.lower() for t in user_allowed}
    matches = article_lower & allowed_lower
    union = article_lower | allowed_lower
    return round(len(matches) / len(union), 4) if union else 0.5


def _triage_article(title: str, content: str, config: Dict[str, Any], user_id: str, source: str, url: str) -> bool:
    if len(content) < 200:
        rprint(f"[yellow]SKIP: Content too short ({len(content)} chars): {title[:40]}...[/yellow]")
        return False

    # Check blocked topics (Negative Filter)
    blocked_topics = config.get("blocked", [])
    text_lower = (title + " " + content).lower()
    if any(b.lower() in text_lower for b in blocked_topics if b):
        rprint(f"[dim]SKIP: Blocked Topic: {title[:40]}...[/dim]")
        log_rejection(user_id, title, source, url, 0.0, "Blocked Topic")
        return False

    # V2 Philosophy: Don't strictly block if allowed keywords are missing.
    # We let it through for semantic evaluation unless it's obviously junk.
    return True


async def _rank_article(
    db: Any,
    h_score: float,
    novelty_score: float,
    quality: float,
    title: str,
    content: str,
    source: str,
    config: Dict[str, Any]
) -> tuple[float, List[str]]:
    allowed_topics = config.get("allowed", [])
    blocked_topics = config.get("blocked", [])
    priority_topics = {t.lower() for t in config.get("priority", [])}
    ai_topics = []

    final_score = h_score
    # Only refine if it's within the borderline range.
    if 3.0 <= h_score <= 7.5:
        rprint(f"[cyan]Refining article with AI ({h_score}): {title[:40]}...[/cyan]")
        analysis = await summarizer_main.call_groq_async(title, content, source, allowed_topics)
        ai_topics = analysis.topics
        final_score = scorer.compute_final_score(scorer.RankSignals(
            base_relevance=analysis.score,
            novelty_score=novelty_score,
            source_quality=quality,
            topic_match=_compute_topic_match(ai_topics, allowed_topics),
            priority_boost=1.0 if any(t.lower() in priority_topics for t in ai_topics) else 0.0,
            is_blocked=any(t.lower() in {b.lower() for b in blocked_topics} for t in ai_topics),
        ))
    return final_score, ai_topics


async def _research_and_persist_article(
    loop: Any, agent: Any, content: str, title: str, user_id: str,
    embedding: list[float], source: str, url: str, final_score: float,
    msg_id: str, article: dict, novelty_score: float, event_id: str, ai_topics: List[str]
) -> bool:
    result = await loop.run_in_executor(None, agent.invoke, {
        "article_text": content, "article_title": title, "user_id": user_id, "embedding": embedding
    })
    if result.get("research_failed"):
        rprint(f"[red]RESEARCH FAILED: {title[:40]}... (Skipping)[/red]")
        log_rejection(user_id, title, source, url, final_score, f"Research Failed: {result.get('summary', 'Unknown error')}")
        acknowledge_message(summarizer_main.GROUP_NAME, msg_id)
        return False

    save_success = save_article({
        "user_id": user_id, "source_id": article.get("source_id"), "title": title, "source_url": url, "source": source,
        "content": content, "embedding": embedding, "novelty_score": novelty_score, "event_id": event_id, "score": final_score,
        "summary": result["summary"], "why_it_matters": result["why_it_matters"], "topics": result.get("topics", ai_topics),
        "published_at": article.get("published_at") or None, "v2_processed": True,
    })

    if not save_success:
        logger.error(f"Failed to save processed article: {title}")
        return False

    acknowledge_message(summarizer_main.GROUP_NAME, msg_id)
    rprint(f"[green]PROCESSED: {title[:50]}... [Score: {final_score}][/green]")
    return True


async def process_article_v2(
    db: Any, msg: Dict[str, Any], agent: Any, GROQ_API_KEY: str, semaphore: asyncio.Semaphore
) -> bool:
    art, msg_id = msg["data"], msg["id"]
    user_id, title, source, url = art.get("user_id"), art.get("title", "Untitled"), art.get("source", "Unknown"), art.get("source_url", "")

    async with semaphore:
        try:
            loop = asyncio.get_running_loop()
            content = clean_html(art.get("content", ""))
            config = get_filter_config(user_id)

            if not _triage_article(title, content, config, user_id, source, url):
                acknowledge_message(summarizer_main.GROUP_NAME, msg_id)
                return False

            embedding = await loop.run_in_executor(None, embedder.embed_text, content or title, GROQ_API_KEY)
            if deduplicator.is_near_duplicate(db, embedding, user_id):
                rprint(f"[dim]SKIP: Near-duplicate: {title[:40]}...[/dim]")
                acknowledge_message(summarizer_main.GROUP_NAME, msg_id)
                return False

            novelty_score = novelty.compute_novelty_score(db, embedding, user_id)
            event_id = clusterer.find_or_create_event(db, summarizer_main.get_llm(), embedding, title, user_id)

            quality = get_source_quality(art.get("source_id"), user_id)
            text_lower = (title + " " + content).lower()
            keyword_matches = [t for t in config.get("allowed", []) if t.lower() in text_lower]
            blocked_topics = config.get("blocked", [])
            has_priority = any(t.lower() in {pt.lower() for pt in config.get("priority", [])} for t in keyword_matches)
            is_blocked = any(b.lower() in text_lower for b in blocked_topics if b)

            h_score = scorer.compute_final_score(scorer.RankSignals(
                base_relevance=4.0, novelty_score=novelty_score, source_quality=quality,
                topic_match=0.6 if keyword_matches else 0.4, priority_boost=1.0 if has_priority else 0.0, 
                is_blocked=is_blocked,
            ))

            final_score, ai_topics = await _rank_article(db, h_score, novelty_score, quality, title, content, source, config)

            if final_score < settings.delivery_threshold:
                rprint(f"[dim]REJECT: Score {final_score:.1f}: {title[:40]}...[/dim]")
                if final_score >= 2.0:
                    log_rejection(user_id, title, source, url, final_score, "Below delivery threshold")
                acknowledge_message(summarizer_main.GROUP_NAME, msg_id)
                return False

            return await _research_and_persist_article(
                loop, agent, content, title, user_id, embedding, source, url,
                final_score, msg_id, art, novelty_score, event_id, ai_topics
            )

        except Exception as e:
            logger.exception(f"Failed to process V2 pipeline for '{title}': {e}")
            return False


async def _run_enrichment_stage(db: Any, GROQ_API_KEY: str, limit: int) -> None:
    messages = read_from_group(summarizer_main.GROUP_NAME, summarizer_main.CONSUMER_NAME, count=limit)
    if not messages:
        rprint("[yellow]No new articles to process.[/yellow]")
        return

    rprint(f"[blue]Processing {len(messages)} articles concurrently (limit: {settings.max_concurrency})...[/blue]")
    agent = build_research_agent(db, GROQ_API_KEY)
    semaphore = asyncio.Semaphore(settings.max_concurrency)

    tasks = [process_article_v2(db, msg, agent, GROQ_API_KEY, semaphore) for msg in messages]
    results = await asyncio.gather(*tasks)

    success_count = sum(1 for r in results if r)
    rprint(f"[bold green]Enrichment batch complete: {success_count}/{len(messages)} articles processed.[/bold green]")


async def _run_delivery_stage(db: Any) -> None:
    from shared.db import get_tenant_profiles
    profiles = get_tenant_profiles()
    delivery_targets = [p["user_id"] for p in profiles]
    loop = asyncio.get_running_loop()

    for user_id in delivery_targets:
        digest = await loop.run_in_executor(None, compose_digest, db, summarizer_main.get_llm(), user_id)
        if not digest.get("empty"):
            await loop.run_in_executor(None, deliver, None, digest)
            rprint(f"[green]Digest delivered to user {user_id}[/green]")
        else:
            rprint(f"[yellow]No items above delivery threshold for user {user_id}[/yellow]")


async def run_all_async(db: Any, limit: int = 50) -> None:
    """Internal async orchestrator for the full V2 pipeline."""
    from rich.console import Console
    console = Console()
    console.rule("[bold cyan]TechPulse AI V2 Pipeline Orchestration")
    GROQ_API_KEY = settings.groq_api_key
    loop = asyncio.get_running_loop()

    ensure_group_exists(summarizer_main.GROUP_NAME)
    console.rule("[dim]Stage 1: Collect", align="left")
    await loop.run_in_executor(None, collect)

    console.rule("[dim]Stage 2-5: Personal Intelligence Enhancement", align="left")
    await _run_enrichment_stage(db, GROQ_API_KEY, limit)

    console.rule("[dim]Stage 6: Multi-Channel Delivery", align="left")
    await _run_delivery_stage(db)

    rprint("\n[bold green]Full TechPulse V2 pipeline sequence complete.[/bold green]")
