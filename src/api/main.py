from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routes import articles, sources, config, pipeline, search

app = FastAPI(
    title="TechPulse API",
    description="REST API for TechPulse curated intelligence and management.",
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

@app.get("/health")
def health_check():
    """System health check endpoint."""
    return {"status": "healthy", "service": "techpulse-api"}

# Include routers
app.include_router(articles.router, prefix="/articles", tags=["Articles"])
app.include_router(sources.router, prefix="/sources", tags=["Sources"])
app.include_router(config.router, prefix="/config", tags=["Configuration"])
app.include_router(pipeline.router, prefix="/pipeline", tags=["Pipeline"])
app.include_router(search.router, prefix="/search", tags=["Search"])

