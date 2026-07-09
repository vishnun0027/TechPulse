import jwt
import sys
import os
from fastapi import Header, HTTPException, status
from typing import Optional
from loguru import logger
from shared.config import settings

def get_current_user_id(
    authorization: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None)
) -> str:
    """
    Dependency that extracts and verifies the user_id from the 'Authorization' Bearer JWT token.
    Falls back to 'X-User-Id' header in test/development environments.
    """
    # Check if we are running in a test, pytest, or development sandbox environment
    is_sandbox = (
        not settings.jwt_secret
        or "sqlite" in settings.database_url
        or "pytest" in sys.modules
        or "PYTEST_CURRENT_TEST" in os.environ
    )

    if not authorization or not authorization.startswith("Bearer "):
        if is_sandbox and x_user_id:
            logger.debug(f"Auth fallback: using X-User-Id header: {x_user_id}")
            return x_user_id

        logger.warning("Request missing or invalid Authorization header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization Bearer token is required.",
        )

    try:
        parts = authorization.split(" ")
        if len(parts) != 2:
            raise jwt.InvalidTokenError("Token format must be Bearer <token>")
        token = parts[1]

        secret = settings.jwt_secret or settings.encryption_key
        if not secret:
            logger.error("JWT_SECRET is not configured on the server")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Auth configuration error."
            )

        payload = jwt.decode(token, secret, algorithms=["HS256"])
        user_id = payload.get("sub") or payload.get("user_id")
        if not user_id:
            raise jwt.InvalidTokenError("Token payload missing subject/user_id claim")
        return str(user_id)
    except jwt.ExpiredSignatureError:
        logger.warning("Token signature has expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired."
        )
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid token: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token."
        )
