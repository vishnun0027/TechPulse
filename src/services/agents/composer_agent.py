from datetime import datetime, timezone
from groq import Groq
from supabase import Client
from loguru import logger
from services.ranker.scorer import DELIVERY_THRESHOLD, BREAKING_THRESHOLD

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
    "Research": ["paper", "arxiv", "benchmark", "study", "dataset", "model"],
    "Quiet Signals": [],  # catch-all for low-score but novel items
}


def assign_theme(article: dict) -> str:
    """Assigns an emoji-prefixed theme based on article content."""
    text = (article.get("title", "") + " " + article.get("summary", "")).lower()
    for theme, keywords in SECTION_THEMES.items():
        if keywords and any(kw in text for kw in keywords):
            return theme
    return "Quiet Signals"


def compose_digest(
    supabase: Client, groq_client: Groq, user_id: str, top_n: int = 12
) -> dict:
    """
    Fetches top undelivered articles for a user, groups them
    into thematic sections, and generates a narrative intro.
    Returns a structured digest dict.
    """
    try:
        # Fetch top-ranked, undelivered articles
        # Note: the blueprint uses 'delivered' but existing schema uses 'is_delivered'
        # I'll use 'is_delivered' to match existing DB, but I'll check migrations again.
        # Wait, my migrations added 'v2_processed' but didn't change 'is_delivered'.
        # Fetch all undelivered articles with a score above threshold (no limit yet)
        # We fetch all so we can apply a "seniority boost" to older articles
        result = (
            supabase.table("articles")
            .select(
                "id, title, summary, why_it_matters, source_url, score, novelty_score, created_at"
            )
            .eq("user_id", user_id)
            .eq("is_delivered", False)
            .gte("score", DELIVERY_THRESHOLD)
            .order("score", desc=True)
            .execute()
        )

        all_pending = result.data or []
        if not all_pending:
            return {"empty": True}

        # Apply "Seniority Boost": +1.0 point for every 24 hours undelivered
        # This guarantees that even low-score articles eventually rise to the top 10
        now = datetime.now(timezone.utc)
        for article in all_pending:
            created_at = datetime.fromisoformat(article["created_at"].replace("Z", "+00:00"))
            age_hours = (now - created_at).total_seconds() / 3600
            age_boost = age_hours / 24.0  # 1 point per day
            article["virtual_score"] = article.get("score", 0) + age_boost

        # Sort by virtual score and take top_n
        all_pending.sort(key=lambda x: x["virtual_score"], reverse=True)
        articles = all_pending[:top_n]

        # Group into themes
        sections: dict[str, list] = {theme: [] for theme in SECTION_THEMES}
        for article in articles:
            theme = assign_theme(article)
            sections[theme].append(article)

        # Remove empty sections
        sections = {k: v for k, v in sections.items() if v}

        # Generate digest narrative intro via LLM
        article_titles = "\n".join([f"- {a['title']}" for a in articles[:8]])
        prompt = f"""Write a 2-sentence tech briefing intro for these stories.
Be direct, no fluff. Start with the most important theme.
Stories:\n{article_titles}"""

        intro_response = groq_client.invoke(prompt)
        intro = intro_response.content.strip()

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
