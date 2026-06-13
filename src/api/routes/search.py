from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from api.deps import get_current_user_id
from shared.db import supabase
from shared.config import settings
from services.agents.rag_agent import build_rag_agent


router = APIRouter()

class RAGSearchRequest(BaseModel):
    query: str
    limit: Optional[int] = 5

class RAGSource(BaseModel):
    id: Optional[str]
    title: str
    summary: Optional[str]
    why_it_matters: Optional[str]
    published_at: Optional[str]
    similarity: float

class RAGSearchResponse(BaseModel):
    query: str
    answer: str
    sources: List[RAGSource]

@router.post("/rag", response_model=RAGSearchResponse)
def perform_rag_search(
    request: RAGSearchRequest,
    user_id: str = Depends(get_current_user_id)
):
    """
    Performs a vector search and cited LLM synthesis (RAG) 
    over the user's personal articles catalog.
    """
    groq_api_key = settings.groq_api_key
    if not groq_api_key:
        raise HTTPException(
            status_code=500, 
            detail="GROQ_API_KEY is not configured on the server."
        )

    # Compile the LangGraph RAG search agent
    agent = build_rag_agent(supabase, groq_api_key)

    # Initialize agent state
    initial_state = {
        "query": request.query,
        "user_id": user_id,
        "query_embedding": None,
        "retrieved_articles": None,
        "synthesized_response": None,
        "sources": None,
        "error": None
    }

    try:
        # Execute workflow synchronously
        result = agent.invoke(initial_state)
        
        # Check for node level errors
        if result.get("error"):
            raise HTTPException(status_code=500, detail=result["error"])

        return {
            "query": request.query,
            "answer": result.get("synthesized_response", ""),
            "sources": result.get("sources") or []
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"RAG search execution failed: {str(e)}"
        )
