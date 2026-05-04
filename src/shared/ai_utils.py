import asyncio
from typing import Any, Callable, TypeVar, cast
import functools
from loguru import logger
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)
from groq import RateLimitError
import httpx
from langchain_core.exceptions import OutputParserException

T = TypeVar("T")

# Specific transient errors that are safe to retry.
# Deliberately NOT including bare `Exception` - that masks real bugs.
_TRANSIENT_ERRORS = (
    RateLimitError,
    OutputParserException,
    asyncio.TimeoutError,
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
    ConnectionError,
    TimeoutError,
)


def retry_llm_call(
    max_attempts: int = 3,
    min_wait: int = 2,
    max_wait: int = 30,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator that applies exponential backoff retries to synchronous LLM calls.
    Only retries on known transient errors (rate limits, timeouts, connection issues).
    Does NOT retry on logic errors like KeyError, ValueError, or AttributeError.
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
            retry=retry_if_exception_type(_TRANSIENT_ERRORS),
            before_sleep=before_sleep_log(logger, "WARNING"),
            reraise=True,
        )
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            return func(*args, **kwargs)

        return cast(Callable[..., T], wrapper)

    return decorator


def strip_thinking(text: str) -> str:
    """Removes <think> reasoning blocks from LLM responses."""
    import re
    return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()


def clean_llm_json(text: str) -> str:
    """
    Cleans LLM response text for JSON parsing:
    1. Strips <think> reasoning blocks.
    2. Extracts the first block enclosed in { }.
    """
    text = strip_thinking(text)
    
    # 2. Extract JSON payload
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1:
        text = text[start:end+1]
    
    return text.strip()


def async_retry_llm_call(
    max_attempts: int = 3,
    min_wait: int = 2,
    max_wait: int = 30,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorator that applies exponential backoff retries to async LLM calls.
    Only retries on known transient errors.
    Note: This is a regular def (not async) - decorators don't need to be async.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
            retry=retry_if_exception_type(_TRANSIENT_ERRORS),
            before_sleep=before_sleep_log(logger, "WARNING"),
            reraise=True,
        )
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await func(*args, **kwargs)

        return wrapper

    return decorator
