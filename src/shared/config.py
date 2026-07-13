from pydantic_settings import BaseSettings
from pydantic import ConfigDict, Field


class Settings(BaseSettings):
    """
    Global application settings loaded from environment variables or .env file.
    """

    model_config = ConfigDict(env_file=".env", extra="ignore")

    # Groq AI Settings
    groq_api_key: str = Field(
        "", description="API key for Groq Cloud (Required for Summarizer)"
    )
    groq_model: str = Field(
        "llama-3.1-8b-instant", description="Model ID to use for summarization"
    )
    groq_research_model: str = Field(
        "meta-llama/llama-4-scout-17b-16e-instruct", description="Model ID to use for deep-dive research"
    )

    # NVIDIA AI Settings
    nvidia_api_key: str = Field(
        "", alias="NVIDIA_API_KEY", description="API key for NVIDIA API Catalog (Optional)"
    )
    nvidia_model: str = Field(
        "meta/llama-3.3-70b-instruct", alias="NVIDIA_MODEL", description="Model ID to use for NVIDIA LLM"
    )

    # Supabase Settings (Backend Data & Auth)
    supabase_url: str = Field(..., description="Supabase project URL")
    supabase_service_key: str = Field(..., alias="SUPABASE_KEY", description="Supabase service role key (Secret)")
    supabase_anon_key: str = Field("", alias="SUPABASE_ANON_KEY", description="Supabase anon/public key")
    database_url: str = Field("", description="Supabase Direct PostgreSQL URL")
    api_base_url: str = Field("http://localhost:8000", alias="API_BASE_URL", description="Base URL of the TechPulse API server")
    encryption_key: str = Field("", alias="ENCRYPTION_KEY", description="Symmetric encryption key for DB secrets")
    jwt_secret: str = Field("", alias="JWT_SECRET", description="JWT signing and verification secret")
    tavily_api_key: str = Field("", alias="TAVILY_API_KEY", description="Tavily Search API key")
    enable_web_search: bool = Field(False, alias="ENABLE_WEB_SEARCH", description="Toggle web search in research agent")

    # Upstash Redis Settings (Pipeline Queue & Cache)
    upstash_redis_rest_url: str = Field("", description="Upstash Redis REST URL")
    upstash_redis_rest_token: str = Field("", description="Upstash Redis REST token")
    redis_url: str = Field("", description="Standard TCP Redis URL")

    # Pipeline Tuning
    top_n_articles: int = Field(
        12, description="Number of top articles to fetch per delivery run"
    )
    dedup_ttl_days: int = Field(
        7, description="How long to remember seen article URLs in Redis"
    )
    collection_interval_days: int = Field(
        14, description="Strict cutoff for article freshness (days)"
    )

    # V2 Logic Thresholds
    near_duplicate_threshold: float = Field(
        0.92, description="Cosine similarity above which articles are duplicates"
    )
    delivery_threshold: float = Field(
        3.5, description="Score above which articles are included in digests"
    )
    breaking_threshold: float = Field(
        8.0, description="Score above which articles trigger immediate alerts"
    )

    # Performance
    max_concurrency: int = Field(
        3, description="Maximum concurrent LLM calls in the pipeline"
    )


# Global settings singleton
settings = Settings()
