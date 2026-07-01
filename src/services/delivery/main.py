import httpx
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from collections import defaultdict
from loguru import logger
from shared.config import settings
from shared.db import (
    supabase,
    mark_as_delivered,
    log_telemetry,
    get_tenant_profiles,
    update_source_delivery,
)
from shared.redis_client import redis, STREAM_RAW

# Theme Configuration is now handled dynamically by the AI in the Summarizer stage.


def group_by_themes(articles: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Groups a list of articles by their calculated themes.

    Args:
        articles: List of article dictionaries.

    Returns:
        Dict[str, List[Dict[str, Any]]]: Grouped articles (max 4 per theme).
    """
    grouped = {}
    for a in articles:
        # The AI now puts the primary theme/category as the FIRST topic in the list
        topics = a.get("topics", [])
        theme = topics[0] if topics else "General Tech"

        if theme not in grouped:
            grouped[theme] = []

        if len(grouped[theme]) < 10:
            grouped[theme].append(a)
    return grouped


# ── Payload Builders ──────────────────────────────────────────────────────────


def _get_summarizer_lag() -> int:
    """Helper to fetch consumer group lag for summarizer-group."""
    try:
        info = redis.execute(command=["XINFO", "GROUPS", STREAM_RAW])
        if info:
            group_data = next((g for g in info if "summarizer-group" in str(g)), None)
            if group_data:
                lag_idx = group_data.index("lag")
                return group_data[lag_idx + 1]
    except Exception:
        pass
    return 0


def _build_slack_article_blocks(grouped_articles: Dict[str, List[Dict[str, Any]]], user_id: str) -> list[dict]:
    """Helper to build Slack Block Kit list of articles partitioned by themes with action links."""
    blocks = []
    for theme, articles in grouped_articles.items():
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*{theme}*"}}
        )
        for a in articles:
            s_title = (a.get("title", "") or "")[:120]
            s_summary = (a.get("summary", "") or "")[:250]
            s_insight = (a.get("why_it_matters", "") or "")[:150]
            s_url = a.get("source_url", "#")
            s_score = a.get("score", 0)
            a_id = a.get("id", "")

            # Redirect link tracking
            encoded_url = urllib.parse.quote(s_url)
            click_url = f"{settings.api_base_url}/articles/{a_id}/click?user_id={user_id}&redirect={encoded_url}"

            # Feedback action links
            like_url = f"{settings.api_base_url}/articles/{a_id}/action?user_id={user_id}&signal=more_like_this"
            dislike_url = f"{settings.api_base_url}/articles/{a_id}/action?user_id={user_id}&signal=less_like_this"
            save_url = f"{settings.api_base_url}/articles/{a_id}/action?user_id={user_id}&signal=saved"

            actions_mrkdwn = f"[ <{like_url}|👍 Like> | <{dislike_url}|👎 Dislike> | <{save_url}|📌 Save> ]"
            narrative = f"_{s_summary}_\n> {actions_mrkdwn}"
            if s_insight:
                narrative += f"\n> *Insight:* {s_insight}"

            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"• <{click_url}|{s_title}>\n  {narrative}",
                    },
                    "accessory": {
                        "type": "button",
                        "text": {"type": "plain_text", "text": f"{s_score}"},
                        "url": click_url,
                    },
                }
            )
        blocks.append({"type": "divider"})
    return blocks


def slack_payload(
    grouped_articles: Dict[str, List[Dict[str, Any]]], user_id: str, intro: Optional[str] = None
) -> Dict[str, Any]:
    """Generates a Slack Block Kit payload for the digest."""
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "TechPulse Smart Digest"},
        }
    ]

    if intro:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": f"_{intro}_"}}
        )

    blocks.append({"type": "divider"})
    total_count = sum(len(v) for v in grouped_articles.values())

    # Build article blocks but respect Slack's 50-block limit
    article_blocks = _build_slack_article_blocks(grouped_articles, user_id)

    # We need to leave room for the footer (1 block) and header/divider (3 blocks)
    # Total limit: 50. Headers/Intro/Divider = 3-4 blocks. Footer = 1 block.
    # Safe limit for article blocks = 45.
    if len(blocks) + len(article_blocks) > 49:
        logger.warning(f"Slack payload exceeds block limit ({len(blocks) + len(article_blocks)}). Truncating.")
        article_blocks = article_blocks[:49 - len(blocks)]
        # Remove trailing divider if any
        if article_blocks and article_blocks[-1]["type"] == "divider":
            article_blocks.pop()

    blocks.extend(article_blocks)

    # Add System Health context footer
    lag = _get_summarizer_lag()
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"*System Health* - {total_count} delivered | {lag} items in processing queue",
                }
            ],
        }
    )

    return {"blocks": blocks}


def discord_payload_chunks(
    grouped_articles: Dict[str, List[Dict[str, Any]]], user_id: str, intro: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Generates Discord Markdown payloads, split into chunks to stay under character limits."""
    chunks = []
    current = "# TechPulse Smart Digest\n\n"
    if intro:
        current += f"*{intro}*\n\n---\n"
    total_count = sum(len(v) for v in grouped_articles.values())

    for theme, articles in grouped_articles.items():
        theme_header = f"## {theme}\n"
        if len(current) + len(theme_header) > 1900:
            chunks.append({"content": current})
            current = theme_header
        else:
            current += theme_header

        for i, a in enumerate(articles, 1):
            insight = (
                f"\n> **Insight:** {a['why_it_matters']}"
                if a.get("why_it_matters")
                else ""
            )
            a_id = a.get("id", "")
            orig_url = a.get("source_url", "#")
            encoded_url = urllib.parse.quote(orig_url)
            click_url = f"{settings.api_base_url}/articles/{a_id}/click?user_id={user_id}&redirect={encoded_url}"

            like_url = f"{settings.api_base_url}/articles/{a_id}/action?user_id={user_id}&signal=more_like_this"
            dislike_url = f"{settings.api_base_url}/articles/{a_id}/action?user_id={user_id}&signal=less_like_this"
            save_url = f"{settings.api_base_url}/articles/{a_id}/action?user_id={user_id}&signal=saved"

            actions = f"[👍 Like]({like_url}) | [👎 Dislike]({dislike_url}) | [📌 Save]({save_url})"

            entry = (
                f"**{i}. [{a['title']}](<{click_url}>)** (Score: {a['score']})\n"
                f"> {a['summary']}{insight}\n"
                f"> Actions: {actions}\n\n"
            )
            if len(current) + len(entry) > 1900:
                chunks.append({"content": current})
                current = entry
            else:
                current += entry

    if current:
        chunks.append({"content": current})

    # Add Stats Footer to the last chunk
    lag = _get_summarizer_lag()
    footer = f"\n---\n**System Health**: {total_count} articles | {lag} items in processing queue"

    if len(chunks[-1]["content"]) + len(footer) < 1950:
        chunks[-1]["content"] += footer
    else:
        chunks.append({"content": footer})

    return chunks


def _get_delivery_data(target_user_id: Optional[str] = None, digest: Optional[Dict[str, Any]] = None) -> dict:
    """Prepares the mapped articles by user for the delivery run."""
    if digest:
        return {digest["user_id"]: digest}

    all_articles = (
        supabase.table("articles")
        .select(
            "id, user_id, title, summary, why_it_matters, source_url, source, score, topics"
        )
        .gte(
            "created_at",
            (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(),
        )
        .eq("is_delivered", False)
        .gte("score", 3.0)
        .execute()
    ).data or []

    if target_user_id:
        all_articles = [a for a in all_articles if a["user_id"] == target_user_id]

    articles_by_user = defaultdict(list)
    for a in all_articles:
        uid = a.get("user_id")
        if uid:
            articles_by_user[uid].append(a)
    return articles_by_user


def _deliver_to_user_webhooks(client: httpx.Client, user_id: str, profile: dict, content: Any, digest: Optional[dict]) -> int:
    """Helper to handle webhook delivery to a single user (Slack + Discord). Returns count delivered."""
    user_name = profile.get("full_name") or "Tech Explorer"

    if digest:
        grouped = digest["sections"]
        intro = digest.get("intro")
    else:
        grouped = group_by_themes(content)
        intro = None

    total_to_send = sum(len(v) for v in grouped.values())
    logger.info(f"Delivering {total_to_send} articles to {user_name} ({user_id})...")

    slack_url = profile.get("slack_webhook_url")
    discord_url = profile.get("discord_webhook_url")

    if not slack_url and not discord_url:
        logger.info(f"User {user_id} has no webhooks configured.")
        return 0

    if slack_url:
        payload = slack_payload(grouped, user_id, intro=intro)
        payload["blocks"][0]["text"]["text"] = f"Hi {user_name}, here is your TechPulse Digest"
        try:
            client.post(slack_url, json=payload, timeout=10).raise_for_status()
            logger.success(f"Slack (User {user_id})")
        except Exception as e:
            logger.error(f"Slack failed for {user_id}: {e}")

    if discord_url:
        chunks = discord_payload_chunks(grouped, user_id, intro=intro)
        if chunks:
            chunks[0]["content"] = chunks[0]["content"].replace("Smart Digest", f"Digest for {user_name}")
        for i, chunk in enumerate(chunks):
            try:
                client.post(discord_url, json=chunk, timeout=10).raise_for_status()
                logger.success(f"Discord chunk {i + 1}/{len(chunks)} (User {user_id})")
            except Exception as e:
                logger.error(f"Discord chunk {i + 1} failed for {user_id}: {e}")

    delivered_urls = [a["source_url"] for theme_list in grouped.values() for a in theme_list]
    mark_as_delivered(delivered_urls, user_id)
    update_source_delivery(delivered_urls, user_id)
    return len(delivered_urls)


def deliver(
    target_user_id: Optional[str] = None, digest: Optional[Dict[str, Any]] = None
) -> None:
    """
    Main delivery entry point.

    If digest is provided, it uses the pre-built narrative structure.
    Otherwise, it fetches pending articles and groups them automatically.
    """
    articles_by_user = _get_delivery_data(target_user_id, digest)
    if not articles_by_user:
        logger.warning("No articles ready to send")
        return

    # Load decrypted profiles if encryption key is configured
    if settings.encryption_key:
        from shared.db import get_decrypted_tenant_profiles
        profiles_list = get_decrypted_tenant_profiles(settings.encryption_key)
    else:
        profiles_list = get_tenant_profiles()
    tenant_profiles = {p["user_id"]: p for p in profiles_list}
    total_delivered_count = 0

    with httpx.Client(timeout=15.0) as client:
        for user_id, content in articles_by_user.items():
            profile = tenant_profiles.get(user_id)
            if not profile:
                logger.warning(f"User {user_id} has articles but no profile, skipping.")
                continue

            delivered_count = _deliver_to_user_webhooks(client, user_id, profile, content, digest)
            total_delivered_count += delivered_count

    log_telemetry(
        "delivery",
        {"count": total_delivered_count, "users_reached": len(articles_by_user)},
    )


if __name__ == "__main__":
    deliver()
