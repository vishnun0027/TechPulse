from langgraph.graph import StateGraph, END
from langchain_groq import ChatGroq
from typing import TypedDict, List, Dict
from supabase import Client
from loguru import logger
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from shared.models import ArticleAnalysis


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


from tenacity import retry, stop_after_attempt, wait_exponential

def retrieve_history(state: ResearchState, supabase: Client) -> ResearchState:
    """Node 1: Pull top-3 related articles from Supabase pgvector."""

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


def build_summary(state: ResearchState, groq_api_key: str) -> ResearchState:
    """Node 2: RAG-enhanced summarization with historical context."""
    # Use higher capacity model for research and lower temperature for JSON stability
    llm = ChatGroq(
        model="llama-3.3-70b-versatile", api_key=groq_api_key, temperature=0.1
    )
    parser = JsonOutputParser(pydantic_object=ArticleAnalysis)

    history_context = ""
    # Ensure similar_history only contains valid dicts
    valid_history = [r for r in state.get("similar_history", []) if isinstance(r, dict)]

    if valid_history:
        history_context = "\n".join(
            [
                f"- [{r.get('published_at', 'recent')[:10]}] {r.get('title', 'Untitled')}: {r.get('why_it_matters', (r.get('summary') or '')[:120])}"
                for r in valid_history
            ]
        )

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are a precise tech analyst. Summarize articles with historical context. {format_instructions}",
            ),
            (
                "human",
                """HISTORICAL CONTEXT:
{history_context}

ARTICLE:
{article_title}
{article_text}""",
            ),
        ]
    )

    chain = prompt | llm | parser

    from shared.ai_utils import retry_llm_call

    @retry_llm_call(max_attempts=3)
    def call_llm(history_context, article_title, article_text, format_instructions):
        return chain.invoke(
            {
                "history_context": history_context,
                "article_title": article_title,
                "article_text": article_text,
                "format_instructions": format_instructions,
            }
        )

    try:
        result = call_llm(
            history_context=history_context or "No prior coverage found.",
            article_title=state["article_title"],
            article_text=state["article_text"][:4000],
            format_instructions=parser.get_format_instructions(),
        )
        # Extract structured results
        state["summary"] = result.get("summary", "")
        state["why_it_matters"] = result.get("why_it_matters", "")
        state["topics"] = result.get("topics", [])
    except Exception as e:
        logger.error(f"Build summary failed for '{state['article_title']}': {e}")
        # Robust fallback with detailed error tracking for the USER
        error_type = type(e).__name__
        state["summary"] = (
            f"⚠️ Summary generation failed ({error_type}).\n\n"
            f"Original Content Preview: {state['article_text'][:300]}..."
        )
        state["why_it_matters"] = f"Error in AI analysis: {str(e)[:100]}"
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
