from datetime import datetime, timezone
from groq import Groq
from supabase import Client
from loguru import logger
from services.ranker.scorer import DELIVERY_THRESHOLD, BREAKING_THRESHOLD
import time
from shared.db import log_ai_inference
from shared.config import settings


SECTION_THEMES = {
    "Generative AI": [
        "llm",
        "gpt",
        "claude",
        "gemini",
        "llama",
        "transformer",
        "fine-tuning",
    ],
    "Developer Tools": [
        "api",
        "sdk",
        "framework",
        "library",
        "release",
        "open source",
        "github",
        "yaml",
        "spec",
        "decoder",
        "compiler",
        "tool",
        "cli",
        "dev",
    ],
    "Industry": ["funding", "acquisition", "startup", "ipo", "layoffs", "valuation"],
    "Security": ["vulnerability", "breach", "cve", "exploit", "patch", "malware"],
    "Regulation": [
        "regulation",
        "policy",
        "gdpr",
        "ban",
        "law",
        "government",
        "compliance",
    ],
    "Research": [
        "paper",
        "arxiv",
        "benchmark",
        "study",
        "dataset",
        "model",
        "research",
        "science",
        "physics",
        "breakthrough",
    ],
    "Quiet Signals": [],  # catch-all for low-score but novel items
}


def assign_theme(article: dict) -> str:
    """Assigns an emoji-prefixed theme based on article content."""
    text = (article.get("title", "") + " " + article.get("summary", "")).lower()
    for theme, keywords in SECTION_THEMES.items():
        if keywords and any(kw in text for kw in keywords):
            return theme
    return "Quiet Signals"


def _fetch_and_boost_articles(supabase: Client, user_id: str, top_n: int) -> list[dict]:
    """Fetches undelivered articles and applies the Seniority Boost algorithm."""
    result = (
        supabase.table("articles")
        .select(
            "id, title, summary, why_it_matters, source_url, score, novelty_score, created_at"
        )
        .eq("user_id", user_id)
        .eq("is_delivered", False)
        .gte("score", DELIVERY_THRESHOLD)
        .order("score", desc=True)
        .limit(200)
        .execute()
    )

    all_pending = result.data or []
    if not all_pending:
        return []

    # Apply "Seniority Boost": +1.0 point for every 24 hours undelivered
    # This guarantees that even low-score articles eventually rise to the top
    now = datetime.now(timezone.utc)
    for article in all_pending:
        created_at = datetime.fromisoformat(article["created_at"].replace("Z", "+00:00"))
        age_hours = (now - created_at).total_seconds() / 3600
        age_boost = age_hours / 24.0  # 1 point per day
        article["virtual_score"] = article.get("score", 0) + age_boost

    # Sort by virtual score and take top_n
    all_pending.sort(key=lambda x: x["virtual_score"], reverse=True)
    return all_pending[:top_n]


def _group_articles_by_theme(articles: list[dict]) -> dict[str, list]:
    """Groups articles into themes with a safety filter check."""
    sections: dict[str, list] = {theme: [] for theme in SECTION_THEMES}
    for article in articles:
        # Safety Filter: Skip items that look like AI failures
        topics = article.get("topics") or []
        summary = article.get("summary", "")

        if "Error" in topics or "generation failed" in summary.lower():
            logger.warning(f"Skipping low-quality article in composer: {article.get('title')}")
            continue

        theme = assign_theme(article)
        sections[theme].append(article)

    # Remove empty sections
    return {k: v for k, v in sections.items() if v}


def compose_digest(
    supabase: Client, groq_client: Groq, user_id: str, top_n: int = 12
) -> dict:
    """
    Fetches top undelivered articles for a user, groups them
    into thematic sections, and generates a narrative intro.
    Returns a structured digest dict.
    """
    try:
        articles = _fetch_and_boost_articles(supabase, user_id, top_n)
        if not articles:
            return {"empty": True}

        sections = _group_articles_by_theme(articles)

        # Generate digest narrative intro via LLM
        article_titles = "\n".join([f"- {a['title']}" for a in articles[:8]])
        prompt = f"""Write a 2-sentence tech briefing intro for these stories.
Be direct, no fluff. Start with the most important theme.
Stories:\n{article_titles}"""

        start_time = time.time()
        intro_response = groq_client.invoke(prompt)
        latency_ms = int((time.time() - start_time) * 1000)

        usage = intro_response.response_metadata.get("token_usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        model_name = intro_response.response_metadata.get("model_name", settings.groq_model)

        log_ai_inference(
            user_id=user_id,
            service="composer_agent",
            model_name=model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )

        from shared.ai_utils import strip_thinking
        intro = strip_thinking(intro_response.content)

        # Check for breaking news
        breaking = [a for a in articles if a.get("score", 0) >= BREAKING_THRESHOLD]

        return {
            "empty": False,
            "intro": intro,
            "breaking": breaking,
            "sections": sections,
            "total": len(articles),
            "user_id": user_id,
        }
    except Exception as e:
        logger.error(f"Compose digest failed: {e}")
        return {"empty": True, "error": str(e)}
