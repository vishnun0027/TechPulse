from supabase import Client
from loguru import logger
import uuid
import json


# articles above this cosine similarity join the same event cluster
CLUSTER_THRESHOLD = 0.85


def _truncate_event_title(title: str, max_words: int = 8) -> str:
    """Truncates an article title to a clean event name without calling an LLM."""
    words = title.strip().split()
    truncated = " ".join(words[:max_words])
    if len(words) > max_words:
        truncated += "…"
    return truncated


def _parse_embedding(embedding: any) -> list[float]:
    """Parses an embedding that might be returned as a string by some Supabase client/PostgREST versions."""
    if isinstance(embedding, list):
        return embedding
    if isinstance(embedding, str):
        try:
            # Handle standard list-string format "[0.1, 0.2, ...]"
            return json.loads(embedding)
        except Exception:
            # Handle possible alternate formats (space separated etc) if needed
            logger.warning(f"Failed to parse embedding string: {embedding[:50]}...")
    return []


def _update_existing_event_centroid(
    supabase: Client, event_id: str, current_count: int, embedding: list[float]
) -> None:
    """Helper to update the centroid of an existing event cluster incrementally."""
    centroid_res = (
        supabase.table("article_events")
        .select("centroid_embedding")
        .eq("id", event_id)
        .execute()
    )

    raw_centroid = (centroid_res.data or [{}])[0].get("centroid_embedding")
    old_centroid = _parse_embedding(raw_centroid) or embedding
    n = current_count

    # Guard against dimension mismatch (e.g. after a model change)
    if len(old_centroid) != len(embedding):
        logger.warning(
            f"Centroid dimension mismatch ({len(old_centroid)} vs {len(embedding)}) "
            f"for event {event_id} - resetting centroid to new embedding."
        )
        new_centroid = embedding
    else:
        new_centroid = [
            round((old_centroid[i] * n + embedding[i]) / (n + 1), 8)
            for i in range(len(embedding))
        ]

    supabase.table("article_events").update(
        {
            "article_count": n + 1,
            "centroid_embedding": new_centroid,
            "updated_at": "now()",
        }
    ).eq("id", event_id).execute()


def _create_fallback_event(
    supabase: Client, user_id: str, article_title: str, embedding: list[float]
) -> str | None:
    """Helper to create a brand new event cluster."""
    try:
        event_title = _truncate_event_title(article_title)
        new_event = (
            supabase.table("article_events")
            .insert(
                {
                    "id": str(uuid.uuid4()),
                    "user_id": user_id,
                    "title": event_title,
                    "centroid_embedding": embedding,
                    "article_count": 1,
                }
            )
            .execute()
        )
        return new_event.data[0]["id"]
    except Exception as e:
        logger.error(f"Failed to create new article event: {e}")
        return None


def find_or_create_event(
    supabase: Client,
    groq_client: any,  # kept for API compatibility but no longer used
    embedding: list[float],
    article_title: str,
    user_id: str,
) -> str | None:
    """
    Finds an existing article_event with a similar centroid embedding,
    or creates a new one. Returns the event_id.
    """
    try:
        # Attempt to find an existing event by centroid similarity
        result = supabase.rpc(
            "match_events_by_centroid",
            {
                "query_embedding": embedding,
                "threshold": CLUSTER_THRESHOLD,
                "p_user_id": user_id,
            },
        ).execute()

        if result.data:
            event_id = result.data[0]["id"]
            current_count = result.data[0].get("article_count", 1)

            _update_existing_event_centroid(supabase, event_id, current_count, embedding)
            return event_id

    except Exception as e:
        # RPC may not exist yet in the DB - log a debug warning, not an error.
        logger.debug(
            f"match_events_by_centroid RPC unavailable, creating new event: {e}"
        )

    # Fallback: create a new event using a truncated article title.
    return _create_fallback_event(supabase, user_id, article_title, embedding)
