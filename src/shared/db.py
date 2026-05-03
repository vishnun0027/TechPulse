from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from shared.config import settings

# Initialize Supabase client
supabase: Client = create_client(settings.supabase_url, settings.supabase_key)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def save_article(article: Dict[str, Any]) -> bool:
    """
    Saves or updates an article in the Supabase 'articles' table.

    Args:
        article: A dictionary containing article fields (title, summary, source_url, etc.).

    Returns:
        bool: True if save/upsert was successful, False otherwise.
    """
    try:
        res = (
            supabase.table("articles")
            .upsert(article, on_conflict="source_url,user_id")
            .execute()
        )
        if not res.data:
            logger.error(f"DB save failed (no data returned): {res}")
            return False
        return True
    except Exception as e:
        logger.error(f"DB save error: {e}")
        return False


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def get_top_articles(limit: int = 10) -> List[Dict[str, Any]]:
    """
    Retrieves high-scoring, undelivered articles from the last 24 hours.

    Args:
        limit: The maximum number of articles to return per user (default: 10).

    Returns:
        List[Dict[str, Any]]: A list of articles ready for delivery.
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    try:
        resp = (
            supabase.table("articles")
            .select("user_id, title, summary, source_url, source, score, topics")
            .gte("created_at", since)
            .eq("is_delivered", False)
            .not_.is_("summary", "null")
            .gte("score", 3.0)
            .order("score", desc=True)
            .limit(500)
            .execute()
        )
        return resp.data or []
    except Exception as e:
        logger.error(f"Error fetching top articles: {e}")
        return []


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=6))
def mark_as_delivered(source_urls: List[str], user_id: str) -> None:
    """
    Marks a batch of articles as delivered in the database for a specific user.

    Args:
        source_urls: List of URLs to mark.
        user_id: The ID of the tenant who received the articles.
    """
    if not source_urls:
        return
    try:
        supabase.table("articles").update({"is_delivered": True}).eq(
            "user_id", user_id
        ).in_("source_url", source_urls).execute()
    except Exception as e:
        logger.error(f"DB update error (mark_delivered): {e}")


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=4))
def log_telemetry(
    service: str,
    metrics: Dict[str, Any],
    user_id: Optional[str] = None,
    success: bool = True,
) -> None:
    """
    Records operational metrics to the 'telemetry' table.

    Args:
        service: Name of the service (e.g., 'collector', 'summarizer').
        metrics: Dictionary of metric values.
        user_id: Optional UUID of the tenant associated with the metric.
        success: Whether the operation was successful.
    """
    try:
        base = {"service": service, "success": success, "metrics": metrics}
        if user_id:
            base["user_id"] = user_id

        for name, val in metrics.items():
            if isinstance(val, (int, float)):
                payload = base.copy()
                payload["metric_name"] = name
                payload["value"] = float(val)
                supabase.table("telemetry").insert(payload).execute()

    except Exception as e:
        logger.error(f"Failed to log telemetry: {e}")


# ── DYNAMIC CONFIGURATION ─────────────────────────────────────────────────────


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def get_rss_sources() -> List[Dict[str, Any]]:
    """
    Fetches all active RSS sources from the database.

    Returns:
        List[Dict[str, Any]]: List of source configurations.
    """
    try:
        res = supabase.table("rss_sources").select("*").eq("is_active", True).execute()
        return res.data or []
    except Exception as e:
        logger.error(f"Error fetching RSS sources: {e}")
        return []


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def get_filter_config(user_id: str) -> Dict[str, List[str]]:
    """
    Retrieves the topic filter configuration for a specific user.

    Args:
        user_id: The unique ID of the tenant.

    Returns:
        Dict[str, List[str]]: Filter configuration dictionary with 'allowed', 'blocked', and 'priority' lists.
    """
    if not user_id:
        return {"allowed": [], "blocked": [], "priority": []}
    try:
        res = (
            supabase.table("app_config")
            .select("value")
            .eq("key", "topics")
            .eq("user_id", user_id)
            .execute()
        )

        if res.data:
            return res.data[0]["value"]
    except Exception as e:
        logger.error(f"Error fetching filter config for {user_id}: {e}")

    return {"allowed": [], "blocked": [], "priority": []}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def get_source_quality(source_id: str, user_id: str) -> float:
    """
    Retrieves the quality score for a specific source from the user's perspective.
    Returns 0.5 (neutral) if no data exists.
    """
    try:
        res = (
            supabase.table("source_health")
            .select("quality_score")
            .eq("source_id", source_id)
            .eq("user_id", user_id)
            .execute()
        )

        if res.data:
            return res.data[0]["quality_score"]
    except Exception as e:
        logger.error(f"Error fetching source quality: {e}")

    return 0.5


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=6))
def update_source_ingestion(source_id: str, user_id: str) -> None:
    """Increment source health counters using atomic RPC."""
    try:
        supabase.rpc(
            "increment_source_ingestion",
            {"p_source_id": source_id, "p_user_id": user_id},
        ).execute()
    except Exception as e:
        logger.error(f"Failed to increment source ingestion: {e}")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def get_tenant_profiles() -> List[Dict[str, Any]]:
    """
    Fetches all registered tenant profiles.

    Returns:
        List[Dict[str, Any]]: List of tenant configuration profiles.
    """
    try:
        res = supabase.table("tenant_profiles").select("*").execute()
        return res.data or []
    except Exception as e:
        logger.error(f"Error fetching tenant profiles: {e}")
        return []


def get_tenant_role(user_id: str) -> str:
    """
    Returns the RBAC role of a tenant.

    Args:
        user_id: The UUID of the tenant.

    Returns:
        str: One of 'admin', 'auditor', 'premium', 'user'. Defaults to 'user' on error.
    """
    try:
        res = (
            supabase.table("tenant_profiles")
            .select("role")
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        return res.data.get("role", "user") if res.data else "user"
    except Exception as e:
        logger.error(f"Error fetching tenant role for {user_id}: {e}")
        return "user"


def get_premium_tenants() -> List[Dict[str, Any]]:
    """
    Returns tenant profiles for admin and premium users only.
    Used by the pipeline to determine who gets advanced AI features
    (e.g., custom scorer weights from app_config).

    Returns:
        List[Dict[str, Any]]: List of admin/premium tenant profiles.
    """
    try:
        res = (
            supabase.table("tenant_profiles")
            .select("*")
            .in_("role", ["admin", "premium"])
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.error(f"Error fetching premium tenants: {e}")
        return []


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=6))
def update_source_delivery(source_urls: List[str], user_id: str) -> None:
    """
    Called after a successful delivery run.

    - Increments articles_delivered for every source that had content sent.
    - Recomputes quality_score = articles_clicked / articles_delivered so the
      ranker's source_quality signal reflects real engagement over time.

    Args:
        source_urls: URLs of the articles that were just delivered.
        user_id:     The tenant ID who received the digest.
    """
    if not source_urls:
        return
    try:
        # Resolve source_ids from the delivered article URLs
        res = (
            supabase.table("articles")
            .select("source_id")
            .eq("user_id", user_id)
            .in_("source_url", source_urls)
            .execute()
        )

        source_ids = list(
            {r["source_id"] for r in (res.data or []) if r.get("source_id")}
        )
        if not source_ids:
            return

        for source_id in source_ids:
            existing = (
                supabase.table("source_health")
                .select("articles_delivered, articles_clicked")
                .eq("source_id", source_id)
                .eq("user_id", user_id)
                .execute()
            )

            if existing.data:
                row = existing.data[0]
                new_delivered = row["articles_delivered"] + 1
                clicked = row["articles_clicked"]
                # quality_score = ratio of clicked vs delivered (0.0-1.0)
                # Stays 0.5 (neutral) until at least one click is recorded
                new_quality = (
                    round(min(clicked / new_delivered, 1.0), 4) if new_delivered > 0 else 0.5
                )

                supabase.table("source_health").update(
                    {
                        "articles_delivered": new_delivered,
                        "quality_score": new_quality,
                        "updated_at": "now()",
                    }
                ).eq("source_id", source_id).eq("user_id", user_id).execute()
            else:
                # First delivery from this source - create the row
                supabase.table("source_health").insert(
                    {
                        "source_id": source_id,
                        "user_id": user_id,
                        "articles_delivered": 1,
                        "articles_clicked": 0,
                        "quality_score": 0.5,
                    }
                ).execute()

            logger.debug(f"source_health updated for source_id={source_id}")

    except Exception as e:
        logger.error(f"Failed to update source delivery stats: {e}")
