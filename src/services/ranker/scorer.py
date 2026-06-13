from dataclasses import dataclass
from loguru import logger
from shared.config import settings


@dataclass
class RankSignals:
    base_relevance: float  # 0–10: LLM relevance score from summarizer
    novelty_score: float  # 0–1: from enricher/novelty.py
    source_quality: float  # 0–1: from source_health table
    topic_match: float  # 0–1: ratio of user topics matching article topics
    priority_boost: float  # 1.0 if article matches a priority topic, else 0.0
    is_blocked: bool = False  # If True, the article matches a negative keyword


# Default weights - aligned with docs/dataflow.md
DEFAULT_WEIGHTS = {
    "base_relevance": 0.35,
    "novelty_score": 0.25,
    "source_quality": 0.20,
    "topic_match": 0.15,
    "priority_boost": 0.05,
}

# Thresholds are read from central config so they can be tuned via .env
# without modifying code.
DELIVERY_THRESHOLD: float = settings.delivery_threshold
BREAKING_THRESHOLD: float = settings.breaking_threshold


def compute_final_score(signals: RankSignals, weights: dict = DEFAULT_WEIGHTS) -> float:
    """
    Returns a final score 0.0–10.0.
    Articles scoring below settings.delivery_threshold are excluded from digests.
    If an article is blocked, it returns 0.0.
    """
    if signals.is_blocked:
        return 0.0

    try:
        score = (
            signals.base_relevance * weights["base_relevance"]
            + signals.novelty_score * weights["novelty_score"] * 10.0
            + signals.source_quality * weights["source_quality"] * 10.0
            + signals.topic_match * weights["topic_match"] * 10.0
            + signals.priority_boost * weights["priority_boost"] * 10.0
        )
        return round(min(score, 10.0), 4)
    except Exception as e:
        logger.error(f"Scoring failed: {e}")
        return 0.0
