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
from services.collector.filter import is_relevant

# Common real browser User-Agents for rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0"
]

def collect() -> None:
    logger.info("Starting collection...")
    total_queued: int = 0
    total_skipped: int = 0
    sources = get_rss_sources()
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.collection_interval_days)

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        for src in sources:
            user_id = src.get("user_id")
            source_name = src.get("name", "Unknown")
            url = src["url"]

            # Surgical Fix: Standardize ArXiv to use their bot-friendly API
            if "arxiv.org/rss/" in url:
                category = url.split("/")[-1]
                url = f"https://export.arxiv.org/api/query?search_query=cat:{category}&sortBy=submittedDate&sortOrder=descending"

            if not user_id:
                continue

            try:
                # Rotate headers to look human
                headers = {
                    "User-Agent": random.choice(USER_AGENTS),
                    "Accept": "application/xml,text/xml,application/xhtml+xml,text/html;q=0.9,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                    "Referer": "https://www.google.com/",
                }

                response = client.get(url, headers=headers)
                response.raise_for_status()
                
                feed = feedparser.parse(response.text)
                
                # Snapshot debugger for failed parses
                if not feed.entries:
                    snippet = response.text[:200].replace("\n", " ").strip()
                    logger.warning(f"[{source_name}] No entries found. Body starts with: {snippet}")
                    continue

                for entry in feed.entries[:15]:
                    # Standard RSS uses 'link', ArXiv API uses 'id'
                    link = entry.get("link") or entry.get("id", "")
                    title = entry.get("title", "")[:300]
                    content = entry.get("summary", "")[:2000]

                    # Freshness check
                    pub_date = entry.get("published_parsed") or entry.get("updated_parsed")
                    if pub_date:
                        dt = datetime.fromtimestamp(calendar.timegm(pub_date), tz=timezone.utc)
                        if dt < cutoff:
                            logger.debug(f"SKIP (Old): {title[:30]}...")
                            continue

                    if not link or check_seen(link, user_id) or check_title_seen(title, user_id):
                        continue

                    if not is_relevant(title, content, user_id):
                        total_skipped += 1
                        continue

                    push_to_stream({
                        "user_id": user_id, "title": title, "source_url": link,
                        "source": source_name, "source_id": src.get("id"), "content": content
                    })
                    mark_seen(link, user_id)
                    mark_title_seen(title, user_id)
                    update_source_ingestion(src.get("id"), user_id)
                    total_queued += 1

                logger.info(f"[{source_name}] done.")
                time.sleep(random.uniform(2.0, 5.0)) # Random jitter to look human

            except Exception as e:
                logger.error(f"[{source_name}] failed: {e}")

    logger.success(f"Collection complete - {total_queued} queued, {total_skipped} skipped")
    log_telemetry("collector", {"total_sources": len(sources), "queued": total_queued, "skipped": total_skipped})

if __name__ == "__main__":
    collect()
