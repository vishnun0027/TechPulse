from fastapi import Header, HTTPException, status
from typing import Optional
from loguru import logger

def get_current_user_id(x_user_id: Optional[str] = Header(None)) -> str:
    """
    Dependency that extracts the user_id from the 'X-User-Id' header.
    In a production system, this would verify a JWT token from Supabase Auth.
    """
    if not x_user_id:
        logger.warning("Request missing X-User-Id header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id header is required for multi-tenancy.",
        )
    return x_user_id
