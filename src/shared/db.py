from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from shared.config import settings

# Initialize Supabase client
supabase: Client = create_client(settings.supabase_url, settings.supabase_service_key)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def save_article(article: Dict[str, Any]) -> Optional[str]:
    """
    Saves or updates an article in the Supabase 'articles' table.

    Args:
        article: A dictionary containing article fields (title, summary, source_url, etc.).

    Returns:
        Optional[str]: The article ID if save/upsert was successful, None otherwise.
    """
    try:
        res = (
            supabase.table("articles")
            .upsert(article, on_conflict="source_url,user_id")
            .execute()
        )
        if not res.data:
            logger.error(f"DB save failed (no data returned): {res}")
            return None
        return res.data[0].get("id")
    except Exception as e:
        logger.error(f"DB save error: {e}")
        return None


def calculate_groq_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Computes pricing in USD based on Groq token costs."""
    model_lower = model.lower()
    if "70b" in model_lower:
        in_cost = 0.59 / 1_000_000
        out_cost = 0.79 / 1_000_000
    elif "8b" in model_lower:
        in_cost = 0.05 / 1_000_000
        out_cost = 0.08 / 1_000_000
    elif "mixtral" in model_lower:
        in_cost = 0.24 / 1_000_000
        out_cost = 0.24 / 1_000_000
    else:
        # Default fallback
        in_cost = 0.15 / 1_000_000
        out_cost = 0.20 / 1_000_000
    return float(prompt_tokens * in_cost + completion_tokens * out_cost)


def log_ai_inference(
    user_id: str,
    service: str,
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    article_id: Optional[str] = None,
    guardrail_status: Optional[str] = "passed",
) -> None:
    """Logs LLM invocation metrics to the 'ai_inference_logs' table."""
    try:
        cost = calculate_groq_cost(model_name, prompt_tokens, completion_tokens)
        payload = {
            "user_id": user_id,
            "service": service,
            "model_name": model_name,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": cost,
            "latency_ms": latency_ms,
            "guardrail_status": guardrail_status,
        }
        if article_id:
            payload["article_id"] = article_id

        supabase.table("ai_inference_logs").insert(payload).execute()
    except Exception as e:
        logger.error(f"Failed to log AI inference metrics: {e}")


def save_data_compliance_metadata(
    article_id: str,
    classification: str,
    pii_scan_status: str,
    pii_entities_found: List[str],
) -> bool:
    """Saves PII scan status and content classification metadata."""
    try:
        payload = {
            "article_id": article_id,
            "classification": classification,
            "pii_scan_status": pii_scan_status,
            "pii_entities_found": pii_entities_found,
        }
        res = supabase.table("data_compliance_metadata").upsert(payload).execute()
        return bool(res.data)
    except Exception as e:
        logger.error(f"Failed to save data compliance metadata: {e}")
        return False


def log_rejection(
    user_id: str,
    title: str,
    source: str,
    source_url: str,
    score: float,
    reason: str,
    metadata: Dict[str, Any] = None,
) -> bool:
    """Logs a rejected article for later audit and threshold tuning."""
    try:
        data = {
            "user_id": user_id,
            "title": title,
            "source": source,
            "source_url": source_url,
            "score": score,
            "rejection_reason": reason,
            "metadata": metadata or {},
        }
        supabase.table("pipeline_audit_logs").insert(data).execute()
        return True
    except Exception as e:
        # Don't fail the pipeline if logging fails, but log it.
        logger.warning(f"Failed to log rejection to audit table: {e}")
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


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def get_decrypted_tenant_profiles(encryption_key: str) -> List[Dict[str, Any]]:
    """
    Fetches tenant profiles with decrypted webhook URLs.
    """
    try:
        res = supabase.rpc(
            "get_decrypted_tenant_profiles",
            {"p_key": encryption_key}
        ).execute()
        return res.data or []
    except Exception as e:
        logger.error(f"Error fetching decrypted tenant profiles: {e}")
        return get_tenant_profiles()


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
    Increments articles_delivered and recomputes quality_score using atomic RPC.

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
            supabase.rpc(
                "increment_source_delivery",
                {"p_source_id": source_id, "p_user_id": user_id},
            ).execute()
            logger.debug(f"source_health updated for source_id={source_id}")

    except Exception as e:
        logger.error(f"Failed to update source delivery stats: {e}")


def get_user_centroids(user_id: str) -> tuple[Optional[List[float]], Optional[List[float]]]:
    """
    Fetches the average liked and disliked centroid vectors for the user from Supabase.
    """
    try:
        res = supabase.rpc("get_user_centroids", {"p_user_id": user_id}).execute()
        if res.data:
            liked_str = res.data[0].get("liked_centroid")
            disliked_str = res.data[0].get("disliked_centroid")

            def parse_vector(v_str):
                if not v_str:
                    return None
                if isinstance(v_str, list):
                    return [float(x) for x in v_str]
                cleaned = v_str.strip("[]")
                if not cleaned:
                    return None
                return [float(x) for x in cleaned.split(",")]

            return parse_vector(liked_str), parse_vector(disliked_str)
    except Exception as e:
        logger.error(f"Error fetching user centroids: {e}")
    return None, None
