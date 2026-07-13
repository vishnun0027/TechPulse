from langgraph.graph import StateGraph, END
from shared.ai_utils import get_llm
from typing import TypedDict, List, Dict, Any, Optional
from supabase import Client
from loguru import logger
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from services.enricher.embedder import embed_text


class RAGSearchState(TypedDict):
    query: str
    user_id: str
    query_embedding: Optional[List[float]]
    retrieved_articles: Optional[List[Dict[str, Any]]]
    synthesized_response: Optional[str]
    sources: Optional[List[Dict[str, Any]]]
    error: Optional[str]


def embed_query_node(state: RAGSearchState) -> RAGSearchState:
    """Node 1: Generates the vector embedding for the search query."""
    state["error"] = None
    try:
        state["query_embedding"] = embed_text(state["query"])
    except Exception as e:
        logger.error(f"Failed to generate query embedding: {e}")
        state["error"] = f"Embedding generation failed: {str(e)}"
    return state


def retrieve_context_node(state: RAGSearchState, supabase: Client) -> RAGSearchState:
    """Node 2: Queries Supabase pgvector for similar articles."""
    if state.get("error"):
        return state
    try:
        # We query Supabase's match_articles RPC function
        res = supabase.rpc(
            "match_articles",
            {
                "query_embedding": state["query_embedding"],
                "match_threshold": 0.35,  # Reasonable similarity limit
                "match_count": 5,
                "p_user_id": state["user_id"],
            }
        ).execute()
        state["retrieved_articles"] = res.data or []
        state["sources"] = [
            {
                "id": art.get("id"),
                "title": art.get("title"),
                "summary": art.get("summary"),
                "why_it_matters": art.get("why_it_matters"),
                "published_at": art.get("published_at"),
                "similarity": art.get("similarity"),
            }
            for art in state["retrieved_articles"]
        ]
    except Exception as e:
        logger.error(f"Failed to retrieve articles: {e}")
        state["error"] = f"Retrieval failed: {str(e)}"
        state["retrieved_articles"] = []
        state["sources"] = []
    return state


def synthesize_answer_node(state: RAGSearchState, groq_api_key: str) -> RAGSearchState:
    """Node 3: Synthesizes a response using the ChatGroq model with citations."""
    if state.get("error"):
        state["synthesized_response"] = f"Error occurred during RAG pipeline execution: {state['error']}"
        return state

    articles = state.get("retrieved_articles", [])
    if not articles:
        state["synthesized_response"] = "No relevant articles were found in your library matching this query."
        return state

    # Format the context block for the prompt
    context_str = ""
    for idx, art in enumerate(articles, 1):
        title = art.get("title", "Untitled")
        summary = art.get("summary", "No summary available.")
        why_it_matters = art.get("why_it_matters", "")
        pub_at = art.get("published_at", "unknown date")
        context_str += f"[{idx}] Source: {title} (Date: {pub_at[:10]})\nSummary: {summary}\nWhy it matters: {why_it_matters}\n\n"

    prompt = ChatPromptTemplate.from_messages([
        ("system", (
            "You are TechPulse Assistant, an AI expert analyst specializing in technology intelligence. "
            "Answer the user's query comprehensively and accurately based only on the provided context retrieved from their personal curated articles database.\n\n"
            "Rules:\n"
            "1. Base your answer solely on the provided articles. If the context does not contain enough information to answer, state that clearly.\n"
            "2. Cite your sources using numbered citations like [1], [2], etc., matching the indices of the sources in the context.\n"
            "3. Respond in structured Markdown format with headings, bullet points, or bold text as appropriate."
        )),
        ("human", "CONTEXT:\n{context}\n\nQUERY: {query}")
    ])

    llm = get_llm(model_role="research", temperature=0.2, api_key=groq_api_key)

    chain = prompt | llm | StrOutputParser()

    try:
        answer = chain.invoke({
            "context": context_str,
            "query": state["query"]
        })
        state["synthesized_response"] = answer
    except Exception as e:
        logger.error(f"LLM synthesis failed: {e}")
        state["synthesized_response"] = f"Failed to synthesize answer: {str(e)}"
    return state


def build_rag_agent(supabase: Client, groq_api_key: str):
    """Constructs and compiles the RAG Search LangGraph workflow."""
    graph = StateGraph(RAGSearchState)

    graph.add_node("embed_query", embed_query_node)
    graph.add_node("retrieve_context", lambda s: retrieve_context_node(s, supabase))
    graph.add_node("synthesize_answer", lambda s: synthesize_answer_node(s, groq_api_key))

    graph.set_entry_point("embed_query")
    graph.add_edge("embed_query", "retrieve_context")
    graph.add_edge("retrieve_context", "synthesize_answer")
    graph.add_edge("synthesize_answer", END)

    return graph.compile()
