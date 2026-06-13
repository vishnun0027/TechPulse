from fastapi import APIRouter, Depends, HTTPException
from typing import List
from shared.db import supabase
from api.deps import get_current_user_id
from pydantic import BaseModel, HttpUrl

router = APIRouter()

class SourceResponse(BaseModel):
    id: int
    name: str
    url: str
    is_active: bool
    created_at: str

class SourceCreate(BaseModel):
    name: str
    url: HttpUrl

@router.get("/", response_model=List[SourceResponse])
def get_sources(user_id: str = Depends(get_current_user_id)):
    """Lists all RSS sources for the current user."""
    res = supabase.table("rss_sources").select("*").eq("user_id", user_id).execute()
    return res.data or []

@router.post("/", response_model=SourceResponse)
def add_source(
    source: SourceCreate,
    user_id: str = Depends(get_current_user_id)
):
    """Adds a new RSS source for the current user."""
    data = {
        "user_id": user_id,
        "name": source.name,
        "url": str(source.url),
        "is_active": True
    }
    res = supabase.table("rss_sources").insert(data).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Failed to add source.")
    return res.data[0]

@router.patch("/{source_id}/toggle")
def toggle_source(
    source_id: str,
    user_id: str = Depends(get_current_user_id)
):
    """Toggles the active status of an RSS source."""
    # Check ownership
    res = supabase.table("rss_sources").select("is_active").eq("id", source_id).eq("user_id", user_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Source not found.")

    new_status = not res.data[0]["is_active"]
    supabase.table("rss_sources").update({"is_active": new_status}).eq("id", source_id).eq("user_id", user_id).execute()
    return {"status": "success", "is_active": new_status}

