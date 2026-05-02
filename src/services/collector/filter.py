import time
from typing import List, Dict, Any, Optional
from loguru import logger
from shared.db import get_filter_config

# Cache the config for 5 minutes during a run per user to reduce DB hits
_config_cache: Dict[str, Dict[str, Any]] = {}


def _clean_topic_list(raw_list: List[str]) -> List[str]:
    """
    Cleans a raw list of topics by removing extraneous quotes, backslashes, and whitespace.

    Args:
        raw_list: The raw list of strings from the database.

    Returns:
        List[str]: A sanitized list of keywords.
    """
    return [t.replace('\\"', "").strip('"').strip("'").strip() for t in raw_list if t]


def get_cached_config(user_id: str) -> Dict[str, List[str]]:
    """
    Retrieves and caches the filter configuration for a specific user.

    Args:
        user_id: The tenant ID.

    Returns:
        Dict[str, List[str]]: Sanitized configuration dictionary.
    """
    now = time.time()
    if not user_id:
        return {"allowed": [], "blocked": [], "priority": []}

    if user_id not in _config_cache or now > _config_cache[user_id]["expiry"]:
        raw = get_filter_config(user_id)

        # Robustness: clean all topic lists
        processed = {
            "allowed": _clean_topic_list(raw.get("allowed", [])),
            "blocked": _clean_topic_list(raw.get("blocked", [])),
            "priority": _clean_topic_list(raw.get("priority", [])),
        }

        _config_cache[user_id] = {"data": processed, "expiry": now + 300}

    return _config_cache[user_id]["data"]


def is_relevant(title: str, content: str = "", user_id: Optional[str] = None) -> bool:
    """
    Determines if an article is relevant to a user based on their allowed/blocked topics.

    In V2, this acts as a 'soft gate':
    1. STRICTLY blocks content matching user 'blocked' keywords.
    2. Allows everything else through to the queue for semantic evaluation by the Ranker.
    """
    config = get_cached_config(user_id)
    blocked = config.get("blocked", [])

    # We check both title and the first 300 chars of content
    text = (title + " " + content[:300]).lower()

    # 1. Block irrelevant content first (Strict)
    if any(b.lower() in text for b in blocked if b):
        logger.info(f"Collector BLOCK: {title[:40]}... (matched blocked keyword)")
        return False

    # 2. V2 Strategy: Let everything through for the Ranker to evaluate.
    # Simple keyword matching is too restrictive for an AI-powered pipeline.
    # The Ranker will compute a proper score using embeddings.
    return True
