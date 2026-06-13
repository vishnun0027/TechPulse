import argparse
import sys
from loguru import logger
from shared.redis_client import redis, STREAM_RAW
from shared.db import supabase


def clear_redis():
    """Clear all Redis-based storage (stream and deduplication keys)."""
    logger.info("Clearing Redis...")

    # 1. Clear the stream
    redis.delete(STREAM_RAW)
    logger.debug(f"Deleted stream: {STREAM_RAW}")

    # 2. Destroy the consumer group
    try:
        redis.execute(command=["XGROUP", "DESTROY", STREAM_RAW, "summarizer-group"])
        logger.debug("Destroyed consumer group: summarizer-group")
    except Exception:
        pass  # Group might not exist if it was never created

    # 3. Clear deduplication keys (seen and title) in a single batch command
    seen_keys = redis.keys("seen:*") or []
    title_keys = redis.keys("title:*") or []
    all_keys = seen_keys + title_keys
    if all_keys:
        # Single DEL call with all keys - avoids N separate HTTP requests to Upstash
        redis.execute(["DEL", *all_keys])
        logger.debug(f"Deleted {len(all_keys)} deduplication keys.")

    logger.success("Redis cleared logic applied.")


def clear_db():
    """Clear all rows from the Supabase articles table."""
    logger.info("Clearing Database...")
    try:
        # Standard Supabase hack to delete all rows: filter for all IDs
        # (Assuming ID is not negative)
        res = (
            supabase.table("articles")
            .delete()
            .neq("title", "___NON_EXISTENT_TITLE___")
            .execute()
        )
        logger.success(f"Database cleared. Deleted records: {len(res.data or [])}")
    except Exception as e:
        logger.error(f"Database clear failed: {e}")


def purge_old_data(days: int = 90):
    """Delete data older than the specified retention period."""
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    logger.info(f"Purging data older than {days} days (Cutoff: {cutoff})...")

    try:
        # 1. Purge articles
        res_art = supabase.table("articles").delete().lt("created_at", cutoff).execute()
        art_count = len(res_art.data or [])

        # 2. Purge telemetry
        res_tel = supabase.table("telemetry").delete().lt("timestamp", cutoff).execute()
        tel_count = len(res_tel.data or [])

        logger.success(
            f"Purge complete. Removed {art_count} articles and {tel_count} telemetry rows."
        )
    except Exception as e:
        logger.error(f"Purge failed: {e}")


async def reset():
    """Wipes all pipeline data (Redis + DB) for a clean restart."""
    logger.warning("PROCEEDING WITH FULL STORAGE RESET...")
    clear_redis()
    clear_db()
    logger.success("Master Reset Complete. System is now in a clean state.")


def main():
    parser = argparse.ArgumentParser(description="TechPulse Maintenance Control")
    parser.add_argument("action", choices=["reset", "purge"], help="Action to perform")
    parser.add_argument(
        "--confirm", action="store_true", help="Confirm destructive action"
    )
    parser.add_argument(
        "--days", type=int, default=90, help="Retention period in days (for purge)"
    )

    args = parser.parse_args()

    if args.action == "reset":
        if not args.confirm:
            logger.error(
                "DANGER: This action will delete all data. Run with --confirm to proceed."
            )
            sys.exit(1)

        logger.warning("PROCEEDING WITH FULL STORAGE RESET...")
        clear_redis()
        clear_db()
        logger.success("Master Reset Complete. System is now in a clean state.")

    elif args.action == "purge":
        purge_old_data(args.days)


if __name__ == "__main__":
    main()
