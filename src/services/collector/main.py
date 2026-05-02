import time
import calendar
from datetime import datetime, timedelta, timezone
import feedparser
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


def collect() -> None:
    """
    Executes the multi-tenant collection pipeline.

    1. Fetches all active RSS sources from Supabase.
    2. Parses each feed for new entries.
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

    for src in sources:
        user_id = src.get("user_id")

        # V2 Refactoring: Global Source Distribution (fan-out in collector) is not recommended
        # for scalability. Sources must be associated with a specific tenant.
        if not user_id:
            logger.debug(
                f"Source '{src.get('name', 'Unknown')}' has no user_id. Skipping global fan-out."
            )
            continue

        try:
            feed = feedparser.parse(src["url"])
            if not hasattr(feed, "entries") or not feed.entries:
                logger.warning(
                    f"No entries found or failed to parse feed: {src['url']}"
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
                        continue

                # 2. Deduplication Check (Per user)
                if (
                    not url
                    or check_seen(url, user_id)
                    or check_title_seen(title, user_id)
                ):
                    continue

                # 3. Topic Relevance Check (Per user)
                # Note: V2 uses a 'soft gate' - unmatched articles are allowed through
                # for semantic ranking if they aren't explicitly blocked.
                if not is_relevant(title, content, user_id):
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
                            "source": src.get("name", "Unknown"),
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

            logger.info(f"[{src.get('name', 'Unknown')}] done.")
            time.sleep(0.5)  # Polite pause between sources

        except Exception as e:
            logger.error(f"[{src.get('name', 'Unknown')}] failed: {e}")

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
