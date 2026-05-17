from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List
from loguru import logger
from shared.db import supabase


def get_source_id_map(user_ids: List[str]) -> Dict[Any, Any]:
    """
    Creates a mapping from (user_id, source_name) to source_id.
    This is a workaround because the 'articles' table is currently missing a 'source_id' column.
    """
    source_map = {}
    try:
        res = (
            supabase.table("rss_sources")
            .select("id, user_id, name")
            .in_("user_id", user_ids)
            .execute()
        )
        for item in res.data or []:
            key = (item["user_id"], item["name"])
            source_map[key] = item["id"]
    except Exception as e:
        logger.error(f"Failed to build source ID map: {e}")
    return source_map


def _fetch_recent_feedback(cutoff: str) -> List[Dict[str, Any]]:
    """Helper to fetch user feedback from Supabase since cutoff time."""
    res = (
        supabase.table("user_feedback")
        .select("article_id, signal, user_id, articles(source)")
        .gte("created_at", cutoff)
        .execute()
    )
    return res.data or []


def _aggregate_feedback(feedback_items: List[Dict[str, Any]], source_map: Dict[Any, Any]) -> Dict[Any, Any]:
    """Helper to aggregate positive and negative feedback signals by user/source."""
    aggregates = {}
    for item in feedback_items:
        user_id = item["user_id"]
        article = item.get("articles")
        if not article or not article.get("source"):
            continue

        source_name = article["source"]
        source_id = source_map.get((user_id, source_name))
        if not source_id:
            continue

        key = (user_id, source_id)
        if key not in aggregates:
            aggregates[key] = {"positive": 0, "negative": 0}

        signal = item["signal"]
        if signal in ["clicked", "saved", "more_like_this"]:
            aggregates[key]["positive"] += 1
        elif signal in ["dismissed", "less_like_this"]:
            aggregates[key]["negative"] += 1
    return aggregates


def _update_source_health_records(aggregates: Dict[Any, Any]) -> None:
    """Helper to update source health quality scores in the database."""
    for (user_id, source_id), counts in aggregates.items():
        res_health = (
            supabase.table("source_health")
            .select("articles_delivered, articles_clicked")
            .eq("user_id", user_id)
            .eq("source_id", source_id)
            .execute()
        )

        if not res_health.data:
            delivered = counts["positive"] + counts["negative"]
            clicked = counts["positive"]
            quality = round(min((clicked + 1) / (delivered + 2), 1.0), 4)

            supabase.table("source_health").insert(
                {
                    "user_id": user_id,
                    "source_id": source_id,
                    "articles_delivered": delivered,
                    "articles_clicked": clicked,
                    "quality_score": quality,
                }
            ).execute()
            continue

        health = res_health.data[0]
        new_clicked = health["articles_clicked"] + counts["positive"]
        new_delivered = health["articles_delivered"]
        denom = max(new_delivered, 1)
        new_quality = round(min(new_clicked / denom, 1.0), 4)

        supabase.table("source_health").update(
            {
                "articles_clicked": new_clicked,
                "articles_delivered": new_delivered,
                "quality_score": new_quality,
                "updated_at": "now()",
            }
        ).eq("user_id", user_id).eq("source_id", source_id).execute()


def process_feedback_batch(days: int = 7):
    """
    Processes user feedback from the specified period and updates source health quality scores.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    logger.info(f"Processing user feedback since {cutoff}...")

    try:
        feedback_items = _fetch_recent_feedback(cutoff)
        if not feedback_items:
            logger.info("No recent feedback found to process.")
            return

        logger.info(f"Found {len(feedback_items)} feedback items.")

        user_ids = list({item["user_id"] for item in feedback_items})
        source_map = get_source_id_map(user_ids)

        aggregates = _aggregate_feedback(feedback_items, source_map)
        if not aggregates:
            logger.info("No valid feedback to aggregate.")
            return

        _update_source_health_records(aggregates)
        logger.success(f"Updated source health for {len(aggregates)} pairs.")

    except Exception as e:
        logger.error(f"Failed to process feedback batch: {e}")
        raise
