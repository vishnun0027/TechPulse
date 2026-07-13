import os
os.environ["GROQ_API_KEY"] = "mock-key"
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
from api.main import app
from langchain_core.messages import AIMessage

client = TestClient(app, raise_server_exceptions=True)
TEST_USER_ID = "ab05c507-fd62-44c4-80de-c1b2ae4f0bf2"

@patch("shared.redis_client.get_cached_rag", return_value=None)
@patch("shared.redis_client.set_cached_rag")
@patch("services.agents.rag_agent.embed_text")
@patch("api.routes.search.supabase")
@patch("services.agents.rag_agent.get_llm")
def test_rag_search_success(mock_get_llm, mock_supabase, mock_embed, mock_set_cache, mock_get_cache):
    # Mock query embedding generation
    mock_embed.return_value = [0.1] * 768

    # Mock Supabase RPC response
    mock_rpc_res = MagicMock()
    mock_rpc_res.execute.return_value.data = [
        {
            "id": "article-uuid-1",
            "title": "AI Agents Breakthrough",
            "summary": "AI agents are growing smarter.",
            "why_it_matters": "Changes automation landscape.",
            "published_at": "2026-06-13T00:00:00Z",
            "similarity": 0.88
        }
    ]
    mock_supabase.rpc.return_value = mock_rpc_res

    # Mock get_llm return model instance behavior
    mock_llm_instance = MagicMock()
    mock_llm_instance.invoke.return_value = AIMessage(
        content="Based on your articles, AI agents are indeed growing smarter [1]."
    )
    mock_llm_instance.return_value = AIMessage(
        content="Based on your articles, AI agents are indeed growing smarter [1]."
    )
    mock_get_llm.return_value = mock_llm_instance

    headers = {"X-User-Id": TEST_USER_ID}
    response = client.post("/search/rag", json={"query": "Tell me about AI agents"}, headers=headers)

    assert response.status_code == 200
    res_data = response.json()
    assert res_data["query"] == "Tell me about AI agents"
    assert "AI agents are indeed growing smarter" in res_data["answer"]
    assert len(res_data["sources"]) == 1
    assert res_data["sources"][0]["title"] == "AI Agents Breakthrough"
    assert res_data["sources"][0]["similarity"] == 0.88

    # Verify RPC arguments
    mock_supabase.rpc.assert_called_once_with(
        "match_articles",
        {
            "query_embedding": [0.1] * 768,
            "match_threshold": 0.35,
            "match_count": 5,
            "p_user_id": TEST_USER_ID,
        }
    )

@patch("shared.redis_client.get_cached_rag", return_value=None)
@patch("shared.redis_client.set_cached_rag")
@patch("services.agents.rag_agent.embed_text")
@patch("api.routes.search.supabase")
def test_rag_search_no_articles(mock_supabase, mock_embed, mock_set_cache, mock_get_cache):
    # Mock query embedding generation
    mock_embed.return_value = [0.1] * 768

    # Mock Supabase RPC returning empty data
    mock_rpc_res = MagicMock()
    mock_rpc_res.execute.return_value.data = []
    mock_supabase.rpc.return_value = mock_rpc_res

    headers = {"X-User-Id": TEST_USER_ID}
    response = client.post("/search/rag", json={"query": "Non-existent topic"}, headers=headers)

    assert response.status_code == 200
    res_data = response.json()
    assert res_data["answer"] == "No relevant articles were found in your library matching this query."
    assert len(res_data["sources"]) == 0

def test_rag_search_unauthorized():
    response = client.post("/search/rag", json={"query": "Test query"})
    # Should require x-user-id header
    assert response.status_code == 401
