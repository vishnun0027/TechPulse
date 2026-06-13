from fastapi import APIRouter, Depends, BackgroundTasks
from api.deps import get_current_user_id
from cli.pipeline import run_all_async
from shared.db import supabase
from loguru import logger

router = APIRouter()

@router.post("/run")
async def trigger_pipeline(
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user_id),
    limit: int = 50
):
    """
    Manually triggers the full pipeline (collect → enrich → deliver) in the background.
    """
    logger.info(f"API: Manual pipeline run triggered by user {user_id}")
    
    # We run it in the background to avoid timing out the HTTP request
    background_tasks.add_task(run_all_async, supabase, limit=limit)
    
    return {
        "status": "accepted",
        "message": "Pipeline run started in the background.",
        "limit": limit
    }
