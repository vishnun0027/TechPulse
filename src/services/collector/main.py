import time
import calendar
import random
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


def _fetch_rss_feed(url: str, source_name: str) -> str | None:
    """Helper to fetch feed content with multiple protocol/SSL fallbacks."""
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    response = None
    try:
        with httpx.Client(http2=True, timeout=20.0, follow_redirects=True) as local_client:
            response = local_client.get(url, headers=headers)
    except (httpx.ProtocolError, httpx.RemoteProtocolError):
        logger.debug(f"[{source_name}] HTTP/2 failed, retrying with HTTP/1.1")
        with httpx.Client(http2=False, timeout=20.0, follow_redirects=True) as local_client:
            response = local_client.get(url, headers=headers)
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e):
            logger.warning(f"[{source_name}] SSL verify failed, retrying without verification")
            with httpx.Client(http2=False, timeout=20.0, follow_redirects=True, verify=False) as local_client:
                response = local_client.get(url, headers=headers)
        else:
            raise e

    if not response:
        logger.error(f"[{source_name}] No response received.")
        return None

    if response.status_code == 403:
        logger.warning(f"[{source_name}] 403 Forbidden. Skipping: {url}")
        return None

    response.raise_for_status()

    feed_text = response.text
    if not feed_text and response.content:
        feed_text = response.content.decode("utf-8", errors="replace")
    return feed_text


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


def collect() -> None:
    logger.info("Starting collection...")
    total_queued: int = 0
    total_skipped: int = 0
    sources = get_rss_sources()
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.collection_interval_days)

    for src in sources:
        user_id = src.get("user_id")
        source_name = src.get("name", "Unknown")
        url = src["url"]

        if not user_id:
            continue

        try:
            feed_text = _fetch_rss_feed(url, source_name)
            if not feed_text:
                continue

            feed = feedparser.parse(feed_text)

            if not feed.entries:
                snippet = feed_text[:200].replace("\n", " ").strip()
                logger.warning(f"[{source_name}] No entries found. Body starts with: {snippet}")
                continue

            queued, skipped = _process_feed_entries(feed, src, user_id, source_name, cutoff)
            total_queued += queued
            total_skipped += skipped

            logger.info(f"[{source_name}] done.")
            time.sleep(random.uniform(3.0, 7.0))  # Jitter to look human

        except Exception as e:
            logger.error(f"[{source_name}] failed: {e}")

    logger.success(f"Collection complete - {total_queued} queued, {total_skipped} skipped")
    log_telemetry("collector", {"total_sources": len(sources), "queued": total_queued, "skipped": total_skipped})


if __name__ == "__main__":
    collect()
