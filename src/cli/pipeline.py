"""
techpulse - Pipeline logic
Contains the orchestrator and processing steps for the V2 agentic pipeline.
"""
import asyncio
from cachetools import TTLCache
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
from shared.db import save_article, log_rejection, get_source_quality, get_filter_config, save_data_compliance_metadata
from shared.utils import clean_html
from shared.compliance import scrub_pii, classify_content


def _compute_topic_match(article_topics: list[str], user_allowed: list[str]) -> float:
    if not article_topics or not user_allowed:
        return 0.5
    article_lower = {t.lower() for t in article_topics}
    allowed_lower = {t.lower() for t in user_allowed}
    matches = article_lower & allowed_lower
    union = article_lower | allowed_lower
    return round(len(matches) / len(union), 4) if union else 0.5


USER_CENTROIDS_CACHE = TTLCache(maxsize=128, ttl=600)  # 10 minutes TTL


def get_cached_user_centroids(user_id: str) -> tuple[Any, Any]:
    if user_id not in USER_CENTROIDS_CACHE:
        from shared.db import get_user_centroids
        USER_CENTROIDS_CACHE[user_id] = get_user_centroids(user_id)
    return USER_CENTROIDS_CACHE[user_id]


def cosine_similarity(u: list[float], v: list[float]) -> float:
    if not u or not v:
        return 0.0
    import math
    dot = sum(x * y for x, y in zip(u, v))
    norm_u = math.sqrt(sum(x * x for x in u))
    norm_v = math.sqrt(sum(x * x for x in v))
    if norm_u == 0.0 or norm_v == 0.0:
        return 0.0
    return dot / (norm_u * norm_v)


def compute_semantic_interest_score(embedding: list[float], user_id: str) -> float:
    liked, disliked = get_cached_user_centroids(user_id)
    if not liked and not disliked:
        return 0.0

    score = 0.0
    if liked:
        score += cosine_similarity(embedding, liked)
    if disliked:
        score -= cosine_similarity(embedding, disliked)
    return score


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


async def _research_and_persist_article(
    loop: Any, agent: Any, content: str, title: str, user_id: str,
    embedding: list[float], source: str, url: str, heuristic_score: float,
    msg_id: str, article: dict, novelty_score: float, event_id: str,
    classification: str, pii_scan_status: str, pii_entities_found: List[str],
    config: Dict[str, Any], quality: float, interest_score: float
) -> bool:
    result = await loop.run_in_executor(None, agent.invoke, {
        "article_text": content, "article_title": title, "user_id": user_id, "embedding": embedding
    })
    if result.get("research_failed"):
        rprint(f"[red]RESEARCH FAILED: {title[:40]}... (Skipping)[/red]")
        log_rejection(user_id, title, source, url, heuristic_score, f"Research Failed: {result.get('summary', 'Unknown error')}")
        acknowledge_message(summarizer_main.GROUP_NAME, msg_id)
        return False

    ai_topics = result.get("topics", [])
    ai_score = result.get("score")
    if ai_score is None:
        # Fallback to a default if score wasn't returned by agent
        ai_score = 4.0

    allowed_topics = config.get("allowed", [])
    blocked_topics = config.get("blocked", [])
    priority_topics = {t.lower() for t in config.get("priority", [])}

    # Compute actual Jaccard topic match and priority boosts based on AI-extracted topics
    topic_match = _compute_topic_match(ai_topics, allowed_topics)
    has_priority = any(t.lower() in priority_topics for t in ai_topics)
    is_blocked = any(t.lower() in {b.lower() for b in blocked_topics} for t in ai_topics)

    # Calculate final refined score using the AI relevance score
    final_score = scorer.compute_final_score(scorer.RankSignals(
        base_relevance=ai_score,
        novelty_score=novelty_score,
        source_quality=quality,
        topic_match=topic_match,
        priority_boost=1.0 if has_priority else 0.0,
        semantic_interest_score=interest_score,
        is_blocked=is_blocked,
    ))

    # Reject if the final refined score falls below the delivery threshold
    if final_score < settings.delivery_threshold:
        rprint(f"[dim]REJECT (Refined): Score {final_score:.1f} (Heuristic was {heuristic_score:.1f}): {title[:40]}...[/dim]")
        if final_score >= 2.0:
            log_rejection(user_id, title, source, url, final_score, "Below delivery threshold after AI refinement")
        acknowledge_message(summarizer_main.GROUP_NAME, msg_id)
        return False

    article_id = save_article({
        "user_id": user_id, "source_id": article.get("source_id"), "title": title, "source_url": url, "source": source,
        "content": content, "embedding": embedding, "novelty_score": novelty_score, "event_id": event_id, "score": final_score,
        "summary": result["summary"], "why_it_matters": result["why_it_matters"], "topics": ai_topics,
        "published_at": article.get("published_at") or None, "v2_processed": True,
    })

    if not article_id:
        logger.error(f"Failed to save processed article: {title}")
        return False

    save_data_compliance_metadata(
        article_id=article_id,
        classification=classification,
        pii_scan_status=pii_scan_status,
        pii_entities_found=pii_entities_found,
    )

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

            # Compliance: PII Scrubbing & Classification
            scrubbed_content, content_status, content_entities = scrub_pii(content)
            scrubbed_title, title_status, title_entities = scrub_pii(title)

            pii_entities_found = list(set(content_entities + title_entities))
            pii_scan_status = "scrubbed" if ("scrubbed" in (content_status, title_status)) else "clean"
            classification = classify_content(scrubbed_content + " " + scrubbed_title)

            if not _triage_article(scrubbed_title, scrubbed_content, config, user_id, source, url):
                acknowledge_message(summarizer_main.GROUP_NAME, msg_id)
                return False

            embedding = await loop.run_in_executor(None, embedder.embed_text, scrubbed_content or scrubbed_title, GROQ_API_KEY)
            if deduplicator.is_near_duplicate(db, embedding, user_id):
                rprint(f"[dim]SKIP: Near-duplicate: {scrubbed_title[:40]}...[/dim]")
                acknowledge_message(summarizer_main.GROUP_NAME, msg_id)
                return False

            novelty_score = novelty.compute_novelty_score(db, embedding, user_id)
            event_id = clusterer.find_or_create_event(db, summarizer_main.get_llm(), embedding, scrubbed_title, user_id)

            quality = get_source_quality(art.get("source_id"), user_id)
            text_lower = (scrubbed_title + " " + scrubbed_content).lower()
            keyword_matches = [t for t in config.get("allowed", []) if t.lower() in text_lower]
            blocked_topics = config.get("blocked", [])
            has_priority = any(t.lower() in {pt.lower() for pt in config.get("priority", [])} for t in keyword_matches)
            is_blocked = any(b.lower() in text_lower for b in blocked_topics if b)

            interest_score = compute_semantic_interest_score(embedding, user_id)
            h_score = scorer.compute_final_score(scorer.RankSignals(
                base_relevance=4.0, novelty_score=novelty_score, source_quality=quality,
                topic_match=0.6 if keyword_matches else 0.4, priority_boost=1.0 if has_priority else 0.0,
                semantic_interest_score=interest_score,
                is_blocked=is_blocked,
            ))

            # Early rejection gate based on heuristic score (saves LLM call costs)
            if h_score < settings.delivery_threshold:
                rprint(f"[dim]REJECT (Heuristic): Score {h_score:.1f}: {scrubbed_title[:40]}...[/dim]")
                if h_score >= 2.0:
                    log_rejection(user_id, scrubbed_title, source, url, h_score, "Heuristic below delivery threshold")
                acknowledge_message(summarizer_main.GROUP_NAME, msg_id)
                return False

            return await _research_and_persist_article(
                loop, agent, scrubbed_content, scrubbed_title, user_id, embedding, source, url,
                h_score, msg_id, art, novelty_score, event_id,
                classification, pii_scan_status, pii_entities_found,
                config, quality, interest_score
            )

        except Exception as e:
            logger.exception(f"Failed to process V2 pipeline for '{title}': {e}")
            try:
                from shared.redis_client import move_to_dlq, get_retry_count
                retries = get_retry_count(msg_id)
                if retries > 3:
                    logger.error(f"DLQ: Message {msg_id} failed {retries} times. Moving to DLQ.")
                    move_to_dlq(msg_id, art, str(e))
                else:
                    logger.warning(f"Retry {retries}/3 for message {msg_id}")
            except Exception as re:
                logger.error(f"Failed to handle DLQ process: {re}")
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
    console.rule("[bold cyan]TechPulse V2 Pipeline Orchestration")
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
