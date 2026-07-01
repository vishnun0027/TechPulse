from langgraph.graph import StateGraph, END
from langchain_groq import ChatGroq
from typing import TypedDict, List, Dict, Any
from supabase import Client
from loguru import logger
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from shared.models import ArticleAnalysis
from tenacity import retry, stop_after_attempt, wait_exponential
from shared.ai_utils import retry_llm_call, clean_llm_json
from shared.config import settings
import time
from shared.db import log_ai_inference


class ResearchState(TypedDict):
    article_text: str
    article_title: str
    user_id: str
    embedding: List[float]
    similar_history: List[Dict]
    web_context: str
    summary: str
    why_it_matters: str
    topics: List[str]
    research_failed: bool


def retrieve_history(state: ResearchState, supabase: Client) -> ResearchState:
    """Node 1: Pull top-3 related articles from Supabase pgvector."""
    state["research_failed"] = False # Initialize

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=6))
    def _execute_rpc():
        return supabase.rpc(
            "match_articles",
            {
                "query_embedding": state["embedding"],
                "match_threshold": 0.72,
                "match_count": 3,
                "p_user_id": state["user_id"],
            },
        ).execute()

    try:
        result = _execute_rpc()
        state["similar_history"] = result.data or []
    except Exception as e:
        logger.error(f"Retrieve history failed: {e}")
        state["similar_history"] = []
    return state


def _format_history_context(similar_history: List[Dict]) -> str:
    """Helper to format similar article history for the LLM prompt."""
    valid_history = [r for r in similar_history if isinstance(r, dict)]
    if not valid_history:
        return "No prior coverage found."
    return "\n".join(
        [
            f"- [{(r.get('published_at') or 'recent')[:10]}] {r.get('title', 'Untitled')}: {r.get('why_it_matters', (r.get('summary') or '')[:120])}"
            for r in valid_history
        ]
    )


@retry_llm_call(max_attempts=3)
def _execute_summary_chain(
    llm: ChatGroq,
    parser: JsonOutputParser,
    history_context: str,
    title: str,
    text: str,
    user_id: str,
) -> Dict[str, Any]:
    """Node 2 helper: executes ChatGroq LLM chain with retries and output cleaning."""
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are a precise tech analyst. Summarize technical articles with historical context. "
                "The input may contain technical noise or fragments; focus on the core engineering value and facts. "
                "{format_instructions}",
            ),
            (
                "human",
                "HISTORICAL CONTEXT:\n{history_context}\n\nARTICLE:\n{title}\n{text}",
            ),
        ]
    )
    formatted = prompt.invoke(
        {
            "history_context": history_context,
            "title": title,
            "text": text,
            "format_instructions": parser.get_format_instructions(),
        }
    )

    start_time = time.time()
    response = llm.invoke(formatted.to_messages())
    latency_ms = int((time.time() - start_time) * 1000)

    usage = response.response_metadata.get("token_usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    model_name = response.response_metadata.get("model_name", settings.groq_research_model)

    log_ai_inference(
        user_id=user_id,
        service="research_agent",
        model_name=model_name,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
    )

    return parser.parse(clean_llm_json(response.content))


def build_summary(state: ResearchState, groq_api_key: str) -> ResearchState:
    """Node 2: RAG-enhanced summarization with historical context."""
    # Use high-capacity model for research (default: Qwen 32B for rate-limit efficiency)
    llm = ChatGroq(
        model=settings.groq_research_model, api_key=groq_api_key, temperature=0.1
    )
    parser = JsonOutputParser(pydantic_object=ArticleAnalysis)

    try:
        history_context = _format_history_context(state.get("similar_history", []))

        result = _execute_summary_chain(
            llm=llm,
            parser=parser,
            history_context=history_context,
            title=state["article_title"],
            text=state["article_text"][:4000],
            user_id=state["user_id"],
        )

        # Extract structured results defensively
        if hasattr(result, "dict"): # Pydantic v1
            res_dict = result.dict()
        elif hasattr(result, "model_dump"): # Pydantic v2
            res_dict = result.model_dump()
        else:
            res_dict = result if isinstance(result, dict) else {}

        state["summary"] = res_dict.get("summary", "")
        state["why_it_matters"] = res_dict.get("why_it_matters", "")
        state["topics"] = res_dict.get("topics", [])

        if not state["summary"] or not state["why_it_matters"]:
            raise ValueError("Incomplete AI response")

    except Exception as e:
        logger.error(f"Build summary failed for '{state['article_title']}': {e}")
        state["research_failed"] = True
        state["summary"] = f"Research phase failed: {str(e)}"
        state["why_it_matters"] = "Skipping delivery due to processing error."
        state["topics"] = ["Error"]

    return state


def build_research_agent(supabase: Client, groq_api_key: str):
    """Constructs and compiles the LangGraph research agent."""
    graph = StateGraph(ResearchState)

    graph.add_node("retrieve_history", lambda s: retrieve_history(s, supabase))
    graph.add_node("build_summary", lambda s: build_summary(s, groq_api_key))

    graph.set_entry_point("retrieve_history")
    graph.add_edge("retrieve_history", "build_summary")
    graph.add_edge("build_summary", END)

    return graph.compile()
