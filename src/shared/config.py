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

    # Supabase Settings (Backend Data & Auth)
    supabase_url: str = Field(..., description="Supabase project URL")
    supabase_key: str = Field(..., description="Supabase service role or anon key")

    # Upstash Redis Settings (Pipeline Queue & Cache)
    upstash_redis_rest_url: str = Field("", description="Upstash Redis REST URL")
    upstash_redis_rest_token: str = Field("", description="Upstash Redis REST token")

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
