import os
import pandas as pd
from loguru import logger
from huggingface_hub import HfApi
from shared.db import supabase


def _fetch_export_articles() -> list[dict]:
    """Helper to fetch processed articles from Supabase."""
    res = (
        supabase.table("articles")
        .select(
            "title, summary, why_it_matters, topics, score, source, source_url, published_at, created_at"
        )
        .eq("v2_processed", True)
        .execute()
    )
    return res.data or []


def _prepare_parquet_file(data: list[dict], filename: str) -> bool:
    """Helper to convert articles list to a deduplicated parquet file."""
    df = pd.DataFrame(data)
    initial_count = len(df)
    df.drop_duplicates(subset=["source_url"], keep="first", inplace=True)
    final_count = len(df)

    if initial_count > final_count:
        logger.info(f"Removed {initial_count - final_count} duplicate articles from export.")

    df.to_parquet(filename, index=False)
    logger.info(f"Data saved to temporary file: {filename}")
    return True


def _upload_parquet_to_hf(filename: str, repo_id: str, is_private: bool) -> None:
    """Helper to publish the parquet archive file to the Hugging Face hub."""
    api = HfApi()
    token = os.getenv("HF_TOKEN")
    if not token:
        raise ValueError("HF_TOKEN environment variable not set.")

    api.create_repo(
        repo_id=repo_id,
        token=token,
        repo_type="dataset",
        private=is_private,
        exist_ok=True,
    )

    api.upload_file(
        path_or_fileobj=filename,
        path_in_repo="data/archive.parquet",
        repo_id=repo_id,
        repo_type="dataset",
        token=token,
    )


def export_to_hf(repo_id: str, is_private: bool = True):
    """
    Exports processed articles to a Hugging Face dataset.
    Excludes sensitive user information and raw large content.
    """
    logger.info(f"Starting export to Hugging Face dataset: {repo_id}...")
    temp_file = "techpulse_intelligence_archive.parquet"

    try:
        data = _fetch_export_articles()
        if not data:
            logger.warning("No processed articles found to export.")
            return

        logger.info(f"Retrieved {len(data)} articles from database.")

        _prepare_parquet_file(data, temp_file)

        _upload_parquet_to_hf(temp_file, repo_id, is_private)

        logger.success(f"Successfully exported dataset to https://huggingface.co/datasets/{repo_id}")

    except Exception as e:
        logger.error(f"Failed to export to Hugging Face: {e}")
        raise
    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)
