from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from shared.redis_client import redis


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Apply rate limiting to public click-tracking and feedback endpoints
        path = request.url.path
        if "/articles/" in path and ("/click" in path or "/action" in path):
            client_ip = request.client.host if request.client else "unknown"
            key = f"rate_limit:{client_ip}:{path}"

            if redis:
                try:
                    current = redis.execute(command=["INCR", key])
                    if current is not None:
                        count = int(current)
                        if count == 1:
                            redis.execute(command=["EXPIRE", key, "60"])

                        if count > 30:  # Max 30 requests per minute
                            return Response(
                                "Too Many Requests. Please try again later.",
                                status_code=429,
                            )
                except Exception as e:
                    from loguru import logger
                    logger.warning(f"Rate limiter failed to query Redis: {e}")

        response = await call_next(request)
        return response
