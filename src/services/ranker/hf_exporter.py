import os
import pandas as pd
from loguru import logger
from huggingface_hub import HfApi
from shared.db import supabase


def export_to_hf(repo_id: str, is_private: bool = True):
    """
    Exports processed articles to a Hugging Face dataset.
    Excludes sensitive user information and raw large content.
    """
    logger.info(f"Starting export to Hugging Face dataset: {repo_id}...")

    try:
        # 1. Fetch processed articles
        # We only export articles that have been fully processed by the V2 pipeline
        res = (
            supabase.table("articles")
            .select(
                "title, summary, why_it_matters, topics, score, source, source_url, published_at, created_at"
            )
            .eq("v2_processed", True)
            .execute()
        )

        data = res.data or []
        if not data:
            logger.warning("No processed articles found to export.")
            return

        logger.info(f"Retrieved {len(data)} articles from database.")

        # 2. Convert to DataFrame and then Parquet
        df = pd.DataFrame(data)
        
        # Ensure no duplicates in the archive (e.g. if multiple users saved the same article)
        initial_count = len(df)
        df.drop_duplicates(subset=["source_url"], keep="first", inplace=True)
        final_count = len(df)
        
        if initial_count > final_count:
            logger.info(f"Removed {initial_count - final_count} duplicate articles from export.")
        
        temp_file = "techpulse_intelligence_archive.parquet"
        df.to_parquet(temp_file, index=False)
        logger.info(f"Data saved to temporary file: {temp_file}")

        # 3. Push to Hugging Face
        api = HfApi()
        token = os.getenv("HF_TOKEN")
        if not token:
            raise ValueError("HF_TOKEN environment variable not set.")

        # Create repo if it doesn't exist
        api.create_repo(
            repo_id=repo_id,
            token=token,
            repo_type="dataset",
            private=is_private,
            exist_ok=True
        )

        # Upload the file
        api.upload_file(
            path_or_fileobj=temp_file,
            path_in_repo="data/archive.parquet",
            repo_id=repo_id,
            repo_type="dataset",
            token=token
        )

        logger.success(f"Successfully exported dataset to https://huggingface.co/datasets/{repo_id}")

        # 4. Cleanup
        if os.path.exists(temp_file):
            os.remove(temp_file)

    except Exception as e:
        logger.error(f"Failed to export to Hugging Face: {e}")
        raise
