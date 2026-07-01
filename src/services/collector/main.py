import calendar
import random
import asyncio
from datetime import datetime, timedelta, timezone
import feedparser
import httpx
from loguru import logger
from shared.config import settings
from shared.redis_client import (
    check_seen,
    mark_seen,
    check_title_seen,
    mark_title_seen,
    push_to_stream,
)
from shared.db import (
    log_telemetry,
    get_rss_sources,
    update_source_ingestion,
)
from typing import Any
from services.collector.filter import is_relevant

# Common real browser User-Agents for rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0"
]


async def _fetch_rss_feed_async(client: httpx.AsyncClient, url: str, source_name: str) -> str | None:
    """Helper to fetch feed content with multiple protocol/SSL fallbacks asynchronously."""
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    try:
        response = await client.get(url, headers=headers)
        if response.status_code == 403:
            logger.warning(f"[{source_name}] 403 Forbidden. Skipping: {url}")
            return None
        response.raise_for_status()
        return response.text or (response.content.decode("utf-8", errors="replace") if response.content else "")
    except (httpx.ProtocolError, httpx.RemoteProtocolError, httpx.ConnectError, httpx.ConnectTimeout) as e:
        logger.debug(f"[{source_name}] Connection error {type(e).__name__}, retrying with basic settings")
        try:
            # Fallback for specifically tricky servers that might dislike HTTP/2 or strict SSL
            async with httpx.AsyncClient(http2=False, timeout=20.0, follow_redirects=True, verify=False) as fallback_client:
                response = await fallback_client.get(url, headers=headers)
                response.raise_for_status()
                return response.text
        except Exception as fe:
            logger.error(f"[{source_name}] Fallback also failed: {fe}")
            return None
    except Exception as e:
        logger.error(f"[{source_name}] Request failed: {e}")
        return None


def _process_feed_entries(feed: Any, src: dict, user_id: str, source_name: str, cutoff: datetime) -> tuple[int, int]:
    """Helper to process a batch of feed entries, filter them, and push to Redis."""
    queued = 0
    skipped = 0
    for entry in feed.entries[:15]:
        link = entry.get("link") or entry.get("id", "")
        title = entry.get("title", "")[:300]
        content = entry.get("summary", "")[:2000]

        pub_date = entry.get("published_parsed") or entry.get("updated_parsed")
        pub_date_iso: str | None = None
        if pub_date:
            dt = datetime.fromtimestamp(calendar.timegm(pub_date), tz=timezone.utc)
            if dt < cutoff:
                logger.debug(f"SKIP (Old): {title[:30]}...")
                continue
            pub_date_iso = dt.isoformat()

        if not link or check_seen(link, user_id) or check_title_seen(title, user_id):
            continue

        if not is_relevant(title, content, user_id):
            skipped += 1
            continue

        push_to_stream({
            "user_id": user_id, "title": title, "source_url": link,
            "source": source_name, "source_id": src.get("id"), "content": content,
            "published_at": pub_date_iso or "",
        })
        mark_seen(link, user_id)
        mark_title_seen(title, user_id)
        update_source_ingestion(src.get("id"), user_id)
        queued += 1
    return queued, skipped


async def _collect_source(sem: asyncio.Semaphore, client: httpx.AsyncClient, src: dict, cutoff: datetime) -> tuple[int, int]:
    """Scrapes a single source asynchronously while respecting a concurrency semaphore."""
    user_id = src.get("user_id")
    source_name = src.get("name", "Unknown")
    url = src["url"]

    if not user_id:
        return 0, 0

    async with sem:
        try:
            # Introduce a small random jitter before starting to avoid traffic spikes
            await asyncio.sleep(random.uniform(0.5, 2.0))

            feed_text = await _fetch_rss_feed_async(client, url, source_name)
            if not feed_text:
                return 0, 0

            feed = feedparser.parse(feed_text)

            if not feed.entries:
                snippet = feed_text[:200].replace("\n", " ").strip()
                logger.warning(f"[{source_name}] No entries found. Body starts with: {snippet}")
                return 0, 0

            queued, skipped = _process_feed_entries(feed, src, user_id, source_name, cutoff)
            logger.info(f"[{source_name}] done.")
            return queued, skipped
        except Exception as e:
            logger.error(f"[{source_name}] failed: {e}")
            return 0, 0


async def collect_async() -> None:
    """Async core of the collection service."""
    logger.info("Starting collection (Async)...")
    sources = get_rss_sources()
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.collection_interval_days)

    # Bounded by max_concurrency settings
    sem = asyncio.Semaphore(settings.max_concurrency)

    async with httpx.AsyncClient(http2=True, timeout=20.0, follow_redirects=True) as client:
        tasks = [_collect_source(sem, client, src, cutoff) for src in sources]
        results = await asyncio.gather(*tasks)

    total_queued = sum(r[0] for r in results)
    total_skipped = sum(r[1] for r in results)

    logger.success(f"Collection complete - {total_queued} queued, {total_skipped} skipped")
    log_telemetry("collector", {"total_sources": len(sources), "queued": total_queued, "skipped": total_skipped})


def collect() -> None:
    """Synchronous wrapper for entry points and systemd execution."""
    try:
        asyncio.run(collect_async())
    except RuntimeError:
        new_loop = asyncio.new_event_loop()
        try:
            new_loop.run_until_complete(collect_async())
        finally:
            new_loop.close()


if __name__ == "__main__":
    collect()
