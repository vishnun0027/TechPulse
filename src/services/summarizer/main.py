import asyncio
from typing import List, Dict, Any, Union, Optional
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from loguru import logger
from tenacity import AsyncRetrying, wait_exponential, stop_after_attempt
from shared.config import settings
from shared.redis_client import (
    ensure_group_exists,
    read_from_group,
    acknowledge_message,
)
from shared.db import save_article, log_telemetry, get_filter_config, log_ai_inference
from shared.models import ArticleAnalysis
from shared.dlp import dlp_scan_and_scrub


# Shared models are now used for structured output schema


# ── LangChain Setup (Lazy) ───────────────────────────────────────────────────

_llm = None
_chain = None


def get_llm():
    """Lazy initializer for the LLM (NVIDIA primary with Groq fallback)."""
    global _llm
    if _llm is None:
        from shared.ai_utils import get_llm as factory_get_llm
        _llm = factory_get_llm(model_role="summary", temperature=0.3)
    return _llm


def get_chain():
    """Lazy initializer for the summarization chain."""
    global _chain
    if _chain is None:
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    """You are a senior tech intelligence officer.
Analyze the article and return valid JSON only:
{{
  "score": <float 0.0-10.0>,
  "summary": "<2-3 sentences of core technical takeaway>",
  "why_it_matters": "<1 sentence on specific urgency or impact>",
  "topics": ["<Category>", "<tag1>", "<tag2>"]
}}

The FIRST topic in the list MUST be a concise category (e.g., 'Python', 'Rust', 'Cloud', 'AI Research') based on your analysis.

Target Topics (for scoring relevance): {allowed_topics}

Score criteria (0-10.0):
- Relevance to the Target Topics (0-5.0 pts)
- Technical depth and insight (0-3.0 pts)
- Novelty and importance (0-2.0 pts)""",
                ),
                ("human", "Title: {title}\nSource: {source}\nContent: {content}"),
            ]
        )
        from shared.ai_utils import clean_llm_json
        from langchain_core.runnables import RunnableLambda
        parser = JsonOutputParser(pydantic_object=ArticleAnalysis)
        _chain = prompt | get_llm() | RunnableLambda(lambda x: clean_llm_json(x.content)) | parser
    return _chain


# ── Groq Call With Async Retry ────────────────────────────────────────────────


async def call_groq_async(
    title: str, content: str, source: str, allowed_topics: List[str], user_id: Optional[str] = None
) -> ArticleAnalysis:
    """
    Calls the Groq LLM to analyze an article with exponential backoff retries.

    Args:
        title: Article title.
        content: Article content snippet.
        source: Name of the news source.
        allowed_topics: User's configured interests for relevance scoring.
        user_id: Optional UUID of the tenant to track inference costs.

    Returns:
        ArticleAnalysis: Pydantic model with AI results.
    """
    allowed_str = (
        ", ".join(allowed_topics)
        if allowed_topics
        else "General high-quality tech intelligence"
    )

    system_template = """You are a senior tech intelligence officer.
Analyze the article and return valid JSON only:
{{
  "score": <float 0.0-10.0>,
  "summary": "<2-3 sentences of core technical takeaway>",
  "why_it_matters": "<1 sentence on specific urgency or impact>",
  "topics": ["<Category>", "<tag1>", "<tag2>"]
}}

The FIRST topic in the list MUST be a concise category (e.g., 'Python', 'Rust', 'Cloud', 'AI Research') based on your analysis.

Target Topics (for scoring relevance): {allowed_topics}

Score criteria (0-10.0):
- Relevance to the Target Topics (0-5.0 pts)
- Technical depth and insight (0-3.0 pts)
- Novelty and importance (0-2.0 pts)"""

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_template),
            ("human", "Title: {title}\nSource: {source}\nContent: {content}"),
        ]
    )

    from shared.ai_utils import clean_llm_json
    import time
    parser = JsonOutputParser(pydantic_object=ArticleAnalysis)

    async for attempt in AsyncRetrying(
        wait=wait_exponential(min=10, max=60), stop=stop_after_attempt(3)
    ):
        with attempt:
            formatted = await prompt.ainvoke(
                {
                    "title": title,
                    "source": source,
                    "content": content[:1500],
                    "allowed_topics": allowed_str,
                }
            )

            start_time = time.time()
            response = await get_llm().ainvoke(formatted.to_messages())
            latency_ms = int((time.time() - start_time) * 1000)

            parsed = parser.parse(clean_llm_json(response.content))

            if user_id:
                usage = response.response_metadata.get("token_usage", {})
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
                model_name = response.response_metadata.get("model_name", settings.groq_model)

                log_ai_inference(
                    user_id=user_id,
                    service="summarizer",
                    model_name=model_name,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    latency_ms=latency_ms,
                )

            return ArticleAnalysis(**parsed)


# ── Main Summarizer ───────────────────────────────────────────────────────────

GROUP_NAME = "summarizer-group"
CONSUMER_NAME = "worker-1"


def _check_blocked(d: dict, config: dict) -> bool:
    """Helper to check if the article content matches any blocked keywords."""
    blocked = [t.lower() for t in config.get("blocked", [])]
    text = (d.get("title", "") + " " + d.get("content", "")[:500]).lower()
    return any(b in text for b in blocked if b)


def _calculate_score_and_boost(result: ArticleAnalysis, config: dict) -> float:
    """Helper to compute the final relevance score with interest boosts."""
    final_score: float = result.score or 0.0
    priority = [t.lower() for t in config.get("priority", [])]
    if any(t.lower() in priority for t in result.topics):
        final_score = min(10.0, final_score + 1.5)
        logger.info("Priority Boost (+1.5) applied.")
    return float(final_score)


def _save_summarizer_article(d: dict, result: ArticleAnalysis, final_score: float, user_id: str) -> bool:
    """Helper to persist analyzed article data to Supabase."""
    article_data = {
        "user_id": user_id,
        "title": d.get("title"),
        "source_url": d.get("source_url"),
        "source": d.get("source"),
        "content": d.get("content"),
        "summary": result.summary,
        "why_it_matters": result.why_it_matters,
        "score": final_score,
        "topics": result.topics,
        "v2_processed": True,
    }
    return save_article(article_data)


async def process_message(
    msg: Dict[str, Any], semaphore: asyncio.Semaphore
) -> Union[float, str, None]:
    """Processes a single raw message from the Redis stream with filtering and AI scoring."""
    async with semaphore:
        d = msg["data"]
        msg_id = msg["id"]
        user_id = d.get("user_id")

        try:
            # DLP: Scrub PII from raw content before it goes to any filter, LLM, or DB
            d["title"] = dlp_scan_and_scrub(d.get("title", ""), "title", user_id).scrubbed_text
            d["content"] = dlp_scan_and_scrub(d.get("content", ""), "content", user_id).scrubbed_text

            loop = asyncio.get_running_loop()
            config = await loop.run_in_executor(None, get_filter_config, user_id)

            if _check_blocked(d, config):
                logger.info(f"Blocked in Summarizer: {d.get('title')[:30]}...")
                acknowledge_message(GROUP_NAME, msg_id)
                return "blocked"

            result = await call_groq_async(
                title=d.get("title", ""),
                content=d.get("content", ""),
                source=d.get("source", ""),
                allowed_topics=config.get("allowed", []),
            )

            final_score = _calculate_score_and_boost(result, config)

            if final_score < 3.0:
                logger.info(f"Early rejection (score={final_score:.1f}) - skipping DB: {d.get('title')[:30]}...")
                acknowledge_message(GROUP_NAME, msg_id)
                await asyncio.sleep(3)
                return final_score

            success = await loop.run_in_executor(
                None, _save_summarizer_article, d, result, final_score, user_id
            )

            if success:
                acknowledge_message(GROUP_NAME, msg_id)
                logger.success(f"[score={final_score:.1f}] [{', '.join(result.topics)}] {d.get('title', '')[:50]}")
            else:
                logger.error(f"Failed to save article {msg_id} to DB.")

            await asyncio.sleep(3)
            return final_score

        except Exception as e:
            logger.error(f"Summarize failed for message {msg_id}: {e}")
            try:
                from shared.redis_client import move_to_dlq, get_retry_count
                retries = get_retry_count(msg_id)
                if retries > 3:
                    logger.error(f"DLQ: Summarizer message {msg_id} failed {retries} times. Moving to DLQ.")
                    move_to_dlq(msg_id, d, str(e))
                else:
                    logger.warning(f"Retry {retries}/3 for message {msg_id}")
            except Exception as re:
                logger.error(f"Failed to handle DLQ process in summarizer: {re}")
            await asyncio.sleep(3)
            return None


async def summarize() -> None:
    """
    Main summarization entry point. Reads batches from Redis and handles async execution.
    """
    ensure_group_exists(GROUP_NAME)

    # Read articles from the raw stream group
    messages = read_from_group(GROUP_NAME, CONSUMER_NAME, count=60)
    if not messages:
        logger.info("No new messages in stream")
        return

    logger.info(f"Summarizing {len(messages)} articles (Async)...")

    # We use a semaphore bounded by settings to properly scale concurrency
    semaphore = asyncio.Semaphore(settings.max_concurrency)
    tasks = [process_message(m, semaphore) for m in messages]
    results = await asyncio.gather(*tasks)

    # Telemetry preparation
    processed_scores = [r for r in results if isinstance(r, float)]
    success_count = len(processed_scores)

    avg_score = (
        round(sum(processed_scores) / success_count, 2) if success_count > 0 else 0
    )
    noise_ratio = (
        round(((len(messages) - success_count) / len(messages) * 100), 1)
        if len(messages) > 0
        else 0
    )

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        log_telemetry,
        "summarizer",
        {"avg_score": avg_score, "noise_ratio": noise_ratio},
        None,
        success_count > 0,
    )


if __name__ == "__main__":
    asyncio.run(summarize())
