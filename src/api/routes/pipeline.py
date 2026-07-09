from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from api.deps import get_current_user_id
from cli.pipeline import run_all_async
from shared.db import supabase, get_tenant_role
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
    # Restrict triggering pipeline to admins only
    try:
        role = get_tenant_role(user_id)
        if role != "admin":
            logger.warning(f"Non-admin user {user_id} tried to trigger pipeline run.")
            raise HTTPException(
                status_code=403,
                detail="Forbidden: Only admins can trigger pipeline runs."
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking role for user {user_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to authenticate user privileges."
        )

    logger.info(f"API: Manual pipeline run triggered by user {user_id}")

    # We run it in the background to avoid timing out the HTTP request
    background_tasks.add_task(run_all_async, supabase, limit=limit)

    return {
        "status": "accepted",
        "message": "Pipeline run started in the background.",
        "limit": limit
    }
