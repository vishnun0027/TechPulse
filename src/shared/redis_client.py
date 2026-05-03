import hashlib
from typing import List, Dict, Any
from upstash_redis import Redis
from shared.config import settings
from shared.utils import normalize_url

# Initialize Redis client lazily or handle empty config
redis: Redis = None
if settings.upstash_redis_rest_url and settings.upstash_redis_rest_token:
    redis = Redis(
        url=settings.upstash_redis_rest_url, token=settings.upstash_redis_rest_token
    )

STREAM_RAW = "stream:raw"
DEDUP_TTL = settings.dedup_ttl_days * 86400


def check_seen(url: str, user_id: str) -> bool:
    """
    Checks if a URL has been processed recently by the specific user.

    Args:
        url: The article URL to check.
        user_id: The tenant ID.

    Returns:
        bool: True if the URL fingerprint exists in Redis.
    """
    normalized = normalize_url(url)
    fp = hashlib.md5(normalized.encode()).hexdigest()
    return bool(redis.exists(f"seen:{user_id}:{fp}"))


def mark_seen(url: str, user_id: str) -> None:
    """
    Records a URL as processed for a specific user with a TTL.

    Args:
        url: The article URL to mark.
        user_id: The tenant ID.
    """
    normalized = normalize_url(url)
    fp = hashlib.md5(normalized.encode()).hexdigest()
    redis.setex(f"seen:{user_id}:{fp}", DEDUP_TTL, 1)


def check_title_seen(title: str, user_id: str) -> bool:
    """
    Checks if a similar title has been processed by the user (semantic deduplication).

    Args:
        title: The article title.
        user_id: The tenant ID.

    Returns:
        bool: True if the title slug exists in Redis.
    """
    slug = "".join(e for e in title.lower() if e.isalnum())[:100]
    return bool(redis.exists(f"title:{user_id}:{slug}"))


def mark_title_seen(title: str, user_id: str) -> None:
    """
    Records a title slug as processed for a specific user.

    Args:
        title: The article title.
        user_id: The tenant ID.
    """
    slug = "".join(e for e in title.lower() if e.isalnum())[:100]
    redis.setex(f"title:{user_id}:{slug}", DEDUP_TTL, 1)


def push_to_stream(data: Dict[str, Any]) -> str:
    """
    Pushes article data to the Redis Stream.

    Args:
        data: Dictionary of article fields.

    Returns:
        str: The Redis message ID.
    """
    cmd = ["XADD", STREAM_RAW, "MAXLEN", "~", "500", "*"]
    for k, v in data.items():
        cmd.append(str(k))
        cmd.append(str(v))
    return redis.execute(command=cmd)


def ensure_group_exists(group_name: str) -> None:
    """
    Ensures the consumer group exists for the raw stream.

    Args:
        group_name: The name of the Redis consumer group.
    """
    try:
        redis.execute(
            command=["XGROUP", "CREATE", STREAM_RAW, group_name, "0", "MKSTREAM"]
        )
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            raise e


def read_from_group(
    group_name: str, consumer_name: str, count: int = 10
) -> List[Dict[str, Any]]:
    """
    Reads messages from a consumer group, prioritizing pending messages.

    This follows a robust pattern:
    1. Try reading messages assigned to this consumer but not yet ACKed ('0').
    2. If none, read brand new messages ('>').

    Args:
        group_name: The consumer group name.
        consumer_name: The unique name for this worker instance.
        count: Max number of messages to fetch.

    Returns:
        List[Dict[str, Any]]: A list of messages with 'id' and 'data'.
    """
    # 1. Check pending for this specific consumer
    result = redis.execute(
        command=[
            "XREADGROUP",
            "GROUP",
            group_name,
            consumer_name,
            "COUNT",
            str(count),
            "STREAMS",
            STREAM_RAW,
            "0",
        ]
    )

    # 2. If no pending, read new for the whole group
    if not result or not result[0][1]:
        result = redis.execute(
            command=[
                "XREADGROUP",
                "GROUP",
                group_name,
                consumer_name,
                "COUNT",
                str(count),
                "STREAMS",
                STREAM_RAW,
                ">",
            ]
        )

    if not result:
        return []

    raw_messages = result[0][1]
    messages = []
    for entry in raw_messages:
        msg_id = entry[0]
        fields_list = entry[1]
        fields = {
            fields_list[i]: fields_list[i + 1] for i in range(0, len(fields_list), 2)
        }
        messages.append({"id": msg_id, "data": fields})
    return messages


def acknowledge_message(group_name: str, msg_id: str) -> None:
    """
    Acknowledges successful processing of a stream message.

    Args:
        group_name: The consumer group name.
        msg_id: The Redis message ID to acknowledge.
    """
    redis.execute(command=["XACK", STREAM_RAW, group_name, msg_id])


def delete_from_stream(msg_id: str) -> None:
    """
    Hard deletes a message from the stream.

    Note: Usually acknowledge_message is preferred in consumer group workflows.
    """
    redis.execute(command=["XDEL", STREAM_RAW, msg_id])
