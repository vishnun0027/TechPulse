import time
import calendar
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
from services.collector.filter import is_relevant

# Professional browser headers to bypass bot protection on ArXiv, Anthropic, Substack, etc.
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "max-age=0",
    "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Upgrade-Insecure-Requests": "1",
}


def collect() -> None:
    """
    Executes the multi-tenant collection pipeline.

    1. Fetches all active RSS sources from Supabase.
    2. Parses each feed for new entries using httpx + feedparser.
    3. Handles user-specific sources (Global fan-out is deprecated).
    4. Filters entries and queues them for V2 semantic ranking.
    """
    logger.info("Starting collection...")
    total_queued: int = 0
    total_skipped: int = 0
    sources = get_rss_sources()

    # Calculate cutoff for freshness based on settings
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=settings.collection_interval_days
    )

    # Use a persistent client for connection pooling
    with httpx.Client(headers=BROWSER_HEADERS, timeout=30.0, follow_redirects=True) as client:
        for src in sources:
            user_id = src.get("user_id")
            source_name = src.get("name", "Unknown")

            if not user_id:
                logger.debug(
                    f"Source '{source_name}' has no user_id. Skipping global fan-out."
                )
                continue

            try:
                # Robust fetching with browser spoofing
                response = client.get(src["url"])
                response.raise_for_status()
                
                # Parse the XML content directly
                feed = feedparser.parse(response.text)
                
                if not hasattr(feed, "entries") or not feed.entries:
                    logger.warning(
                        f"No entries found for source: {source_name} ({src['url']})"
                    )
                    continue

                # We only check the top 15 entries per source to keep runs fast
                for entry in feed.entries[:15]:
                    url = entry.get("link", "")
                    title = entry.get("title", "")[:300]
                    content = entry.get("summary", "")[:2000]

                    # 1. Freshness Check
                    pub_date = entry.get("published_parsed")
                    if pub_date:
                        dt = datetime.fromtimestamp(
                            calendar.timegm(pub_date), tz=timezone.utc
                        )
                        if dt < cutoff:
                            logger.debug(f"SKIP (Too Old: {dt}): {title[:30]}...")
                            continue

                    # 2. Deduplication Check (Per user)
                    if not url:
                        continue
                    
                    if check_seen(url, user_id):
                        logger.debug(f"SKIP (Seen URL): {title[:30]}...")
                        continue
                    
                    if check_title_seen(title, user_id):
                        logger.debug(f"SKIP (Seen Title): {title[:30]}...")
                        continue

                    # 3. Topic Relevance Check (Per user)
                    if not is_relevant(title, content, user_id):
                        logger.debug(f"SKIP (Filtered): {title[:30]}...")
                        total_skipped += 1
                        continue

                    # 4. Queue for Summarization
                    try:
                        from shared.utils import normalize_url

                        n_url = normalize_url(url)
                        push_to_stream(
                            {
                                "user_id": user_id,
                                "title": title,
                                "source_url": n_url,
                                "source": source_name,
                                "source_id": src.get("id"),
                                "content": content,
                            }
                        )
                        mark_seen(n_url, user_id)
                        mark_title_seen(title, user_id)
                        update_source_ingestion(src.get("id"), user_id)
                        total_queued += 1
                        logger.debug(f"Queued for {user_id}: {title[:40]}...")

                    except Exception as e:
                        logger.error(f"Failed to push to stream: {e}")

                logger.info(f"[{source_name}] done.")
                time.sleep(1.0)  # Polite pause between sources

            except httpx.HTTPStatusError as e:
                logger.error(f"[{source_name}] HTTP error {e.response.status_code}: {src['url']}")
            except Exception as e:
                logger.error(f"[{source_name}] failed: {e}")


    logger.success(
        f"Collection complete - {total_queued} queued, {total_skipped} skipped"
    )

    # Record telemetry
    log_telemetry(
        "collector",
        {
            "total_sources": len(sources),
            "queued": total_queued,
            "skipped": total_skipped,
        },
    )


if __name__ == "__main__":
    collect()
