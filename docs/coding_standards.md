# TechPulse AI Coding Standards

**Author:** Vishnu
**Last Updated:** 2026-04-28 14:41 UTC

This document outlines the strict coding standards and conventions enforced across the TechPulse AI V2 codebase.

## 1. Code Formatting & Linting
We use **Ruff** as our primary linter and formatter to enforce standard Python PEP8 styles and maximize execution speed.
- **Line Length:** 120 characters maximum (handled automatically by Ruff).
- **Indentation:** 4 spaces (no tabs).
- **Automation:** Always run `uvx ruff check --fix .` and `uvx ruff format .` before pushing code to ensure compliance.

## 2. Professional Output (No Emojis)
To maintain a clean, ASCII-only codebase:
- **No Emojis:** Do not use emojis in Python source code strings, docstrings, system prompts, CLI outputs, or loggers.
- Keep payloads, digests, and terminal logs highly professional. 
- *Exceptions:* Emojis are only permitted in front-end configurations if specifically injected outside of the backend processing logic.

## 3. Asynchronous Programming
The core orchestration and data layers are heavily asynchronous to handle high throughput.
- **Never Block the Event Loop:** Do not call synchronous, blocking functions (like standard `requests` or synchronous Supabase database calls) directly inside an `async def` function.
- **Use Executors:** If you must use a blocking function, wrap it using `asyncio.get_running_loop().run_in_executor(None, func)`.
- **Concurrency:** Avoid hardcoded semaphores (like `asyncio.Semaphore(1)`) unless strictly necessary. Rely on `settings.max_concurrency` for dynamic scaling.

## 4. Type Hinting
- Strict type hinting is enforced across the codebase.
- Always annotate function arguments and return types using the `typing` module (e.g., `List`, `Dict`, `Any`, `Optional`).
- Pydantic models must be used for data validation and LLM structured outputs.

## 5. Testing
- Write tests using `pytest`.
- Maintain test coverage for all new services and core logic in `tests/unit/` and `tests/e2e/`.
- Ensure tests use mocked services (via `unittest.mock.patch`) to prevent tests from hitting live databases or APIs unless strictly necessary for End-to-End checks.

## 6. Error Handling & Retries
- Use the `tenacity` library (e.g., `@retry` or `AsyncRetrying`) for external network calls (Supabase DB operations, Groq LLM calls) to gracefully handle transient failures and rate limits with exponential backoff.
- Never use a bare `except:`. Always catch specific exceptions (like `except ValueError:`) or fallback to `except Exception as e:` and log the error contextually.

## 7. Lazy Loading & Singletons
- LLM chains and clients should be lazily instantiated (e.g., using a `get_llm()` or `get_chain()` pattern) to prevent blocking execution during fast CLI startup times.
