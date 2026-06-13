from fastapi import APIRouter, Depends, Query, HTTPException
from typing import List, Optional
from shared.db import supabase
from api.deps import get_current_user_id
from pydantic import BaseModel

router = APIRouter()

class ArticleResponse(BaseModel):
    id: str
    title: str
    summary: Optional[str]
    why_it_matters: Optional[str]
    source_url: str
    source: str
    score: float
    topics: List[str]
    is_delivered: bool
    created_at: str

class FeedbackRequest(BaseModel):
    signal: str # clicked, saved, dismissed, more_like_this, less_like_this

@router.get("/", response_model=List[ArticleResponse])
def get_articles(
    user_id: str = Depends(get_current_user_id),
    min_score: float = Query(3.5, ge=0.0, le=10.0),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    is_delivered: Optional[bool] = None,
):
    """Fetches curated articles for the current user."""
    query = (
        supabase.table("articles")
        .select("*")
        .eq("user_id", user_id)
        .gte("score", min_score)
        .order("score", desc=True)
        .range(offset, offset + limit - 1)
    )
    
    if is_delivered is not None:
        query = query.eq("is_delivered", is_delivered)
        
    res = query.execute()
    return res.data or []

@router.post("/{article_id}/feedback")
def submit_feedback(
    article_id: str,
    request: FeedbackRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Submits user feedback for an article to power the feedback loop."""
    # Verify article exists and belongs to user (optional but recommended)
    check = supabase.table("articles").select("id").eq("id", article_id).eq("user_id", user_id).execute()
    if not check.data:
        raise HTTPException(status_code=404, detail="Article not found or access denied.")

    try:
        supabase.table("user_feedback").insert({
            "article_id": article_id,
            "user_id": user_id,
            "signal": request.signal
        }).execute()
        return {"status": "success", "message": f"Feedback '{request.signal}' recorded."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to record feedback: {str(e)}")
