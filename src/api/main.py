from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from api.routes import articles, sources, config, pipeline, search
from api.rate_limiter import RateLimitMiddleware
from shared.config import settings
from shared.redis_client import ping_redis
from shared.db import supabase
from loguru import logger
import urllib.parse

app = FastAPI(
    title="TechPulse API",
    description="REST API for TechPulse curated intelligence and management.",
    version="0.1.0",
)

# Configure Rate Limiting
app.add_middleware(RateLimitMiddleware)

# Derive allowed CORS origins dynamically to prevent wildcard CORS in production
allowed_origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
]
if settings.api_base_url:
    try:
        parsed = urllib.parse.urlparse(settings.api_base_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        allowed_origins.append(origin)
        # Also allow frontend domains sharing the parent domain
        if ".nullnex.com" in parsed.netloc:
            allowed_origins.append("https://pulse.nullnex.com")
            allowed_origins.append("https://pulse-api.nullnex.com")
    except Exception as e:
        logger.error(f"Error parsing API_BASE_URL for CORS: {e}")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health_check():
    """System health check endpoint that verifies Redis + Supabase connectivity."""
    redis_healthy = ping_redis()
    
    supabase_healthy = False
    try:
        # Lightweight check: Query tenant profiles count
        supabase.table("tenant_profiles").select("count", count="exact").limit(1).execute()
        supabase_healthy = True
    except Exception as e:
        logger.error(f"Health check failed for Supabase: {e}")

    if not (redis_healthy and supabase_healthy):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "unhealthy",
                "service": "techpulse-api",
                "redis": "healthy" if redis_healthy else "down",
                "supabase": "healthy" if supabase_healthy else "down"
            }
        )

    return {
        "status": "healthy",
        "service": "techpulse-api",
        "redis": "healthy",
        "supabase": "healthy"
    }

# Include routers
app.include_router(articles.router, prefix="/articles", tags=["Articles"])
app.include_router(sources.router, prefix="/sources", tags=["Sources"])
app.include_router(config.router, prefix="/config", tags=["Configuration"])
app.include_router(pipeline.router, prefix="/pipeline", tags=["Pipeline"])
app.include_router(search.router, prefix="/search", tags=["Search"])

