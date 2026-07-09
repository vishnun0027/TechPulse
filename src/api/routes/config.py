from fastapi import APIRouter, Depends, HTTPException
from typing import List
from shared.db import supabase
from api.deps import get_current_user_id
from pydantic import BaseModel
from loguru import logger

router = APIRouter()

class ConfigUpdate(BaseModel):
    allowed: List[str]
    blocked: List[str]
    priority: List[str]

@router.get("/")
def get_config(user_id: str = Depends(get_current_user_id)):
    """Fetches the interest filter configuration for the current user."""
    try:
        res = supabase.table("app_config").select("value").eq("key", "topics").eq("user_id", user_id).execute()
        if not res.data:
            return {"allowed": [], "blocked": [], "priority": []}
        return res.data[0]["value"]
    except Exception as e:
        logger.error(f"Failed to fetch config for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch configuration.")

@router.put("/")
def update_config(
    config: ConfigUpdate,
    user_id: str = Depends(get_current_user_id)
):
    """Updates the interest filter configuration for the current user."""
    data = {
        "user_id": user_id,
        "key": "topics",
        "value": config.model_dump()
    }
    # Upsert based on (user_id, key)
    # Note: app_config might not have a unique constraint on (user_id, key) in all schemas,
    # but logically it should. For now we use upsert if available or update/insert.
    try:
        res = supabase.table("app_config").upsert(data, on_conflict="user_id,key").execute()
        return {"status": "success", "config": res.data[0]["value"]}
    except Exception as e:
        logger.error(f"Failed to update config for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update configuration.")

@router.get("/stats")
def get_user_stats(user_id: str = Depends(get_current_user_id)):
    """Returns high-level stats for the current user."""
    # Count total articles
    articles_res = supabase.table("articles").select("id", count="exact").eq("user_id", user_id).execute()
    total_articles = articles_res.count or 0

    # Count active sources
    sources_res = supabase.table("rss_sources").select("id", count="exact").eq("user_id", user_id).eq("is_active", True).execute()
    active_sources = sources_res.count or 0

    # Get last delivery time
    last_delivery = (
        supabase.table("articles")
        .select("created_at")
        .eq("user_id", user_id)
        .eq("is_delivered", True)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    last_time = last_delivery.data[0]["created_at"] if last_delivery.data else None

    return {
        "total_articles": total_articles,
        "active_sources": active_sources,
        "last_delivery": last_time
    }
