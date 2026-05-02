from supabase import Client
from loguru import logger
import uuid


# articles above this cosine similarity join the same event cluster
CLUSTER_THRESHOLD = 0.85


def _truncate_event_title(title: str, max_words: int = 8) -> str:
    """Truncates an article title to a clean event name without calling an LLM."""
    words = title.strip().split()
    truncated = " ".join(words[:max_words])
    if len(words) > max_words:
        truncated += "…"
    return truncated


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

            # Update centroid using a running (incremental) average:
            #   new_centroid[i] = (old[i] * n + new[i]) / (n + 1)
            # This keeps the centroid representative as the cluster grows.
            # We fetch the current centroid first for the calculation.
            centroid_res = (
                supabase.table("article_events")
                .select("centroid_embedding")
                .eq("id", event_id)
                .execute()
            )

            old_centroid = (centroid_res.data or [{}])[0].get(
                "centroid_embedding"
            ) or embedding
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
                    "last_updated": "now()",
                }
            ).eq("id", event_id).execute()

            return event_id

    except Exception as e:
        # RPC may not exist yet in the DB - log a debug warning, not an error.
        logger.debug(
            f"match_events_by_centroid RPC unavailable, creating new event: {e}"
        )

    # Fallback: create a new event using a truncated article title.
    # Deliberately no LLM call here - saves ~40 Groq API calls per pipeline run.
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
