from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

app = FastAPI(
    title="TechPulse AI API",
    description="REST API for TechPulse AI curated intelligence and management.",
    version="0.1.0",
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Adjust in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from api.routes import articles, sources, config, pipeline, search

@app.get("/health")
def health_check():
    """System health check endpoint."""
    return {"status": "healthy", "service": "techpulse-ai-api"}

# Include routers
app.include_router(articles.router, prefix="/articles", tags=["Articles"])
app.include_router(sources.router, prefix="/sources", tags=["Sources"])
app.include_router(config.router, prefix="/config", tags=["Configuration"])
app.include_router(pipeline.router, prefix="/pipeline", tags=["Pipeline"])
app.include_router(search.router, prefix="/search", tags=["Search"])

