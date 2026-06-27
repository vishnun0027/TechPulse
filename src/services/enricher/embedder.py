import os
import threading
from sentence_transformers import SentenceTransformer
from loguru import logger

# Inject HF_TOKEN into environment so sentence-transformers authenticates
# with HuggingFace Hub during model download, getting higher rate limits.
_hf_token = os.getenv("HF_TOKEN", "")
if _hf_token:
    os.environ.setdefault("HUGGING_FACE_HUB_TOKEN", _hf_token)
    os.environ.setdefault("HF_TOKEN", _hf_token)

# Load model once on first use. Protected by a lock to prevent race conditions
# when parallel asyncio workers call embed_text concurrently via run_in_executor.
MODEL_NAME = "all-mpnet-base-v2"
_model: SentenceTransformer | None = None
_model_lock = threading.Lock()


def get_model() -> SentenceTransformer:
    """
    Returns the singleton embedding model instance.
    Thread-safe: uses a lock to ensure the model is only loaded once even when
    called from multiple parallel threads simultaneously.
    """
    global _model
    if _model is None:
        with _model_lock:
            # Double-checked locking: re-check after acquiring the lock in case
            # another thread loaded the model while we were waiting.
            if _model is None:
                logger.info(f"Loading local embedding model: {MODEL_NAME}...")
                try:
                    _model = SentenceTransformer(MODEL_NAME)
                    logger.success(f"Model {MODEL_NAME} loaded successfully.")
                except Exception as e:
                    logger.error(f"Failed to load embedding model: {e}")
                    raise
    return _model


def embed_text(text: str, api_key: str = None) -> list[float]:
    """
    Returns a 768-dim embedding using the local sentence-transformers model.
    The api_key argument is kept for API compatibility with the V2 blueprint
    but is not used (embeddings are computed locally).
    """
    from shared.redis_client import get_cached_embedding, set_cached_embedding

    cached = get_cached_embedding(text)
    if cached is not None:
        logger.debug("Embedding cache hit.")
        return cached

    model = get_model()
    truncated = text[:4000]
    try:
        embedding = model.encode(truncated)
        emb_list = embedding.tolist()
        set_cached_embedding(text, emb_list)
        return emb_list
    except Exception as e:
        logger.error(f"Local embedding failed: {e}")
        raise
