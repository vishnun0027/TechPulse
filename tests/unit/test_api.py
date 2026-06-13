from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
from api.main import app

client = TestClient(app)
TEST_USER_ID = "ab05c507-fd62-44c4-80de-c1b2ae4f0bf2"

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "service": "techpulse-ai-api"}

@patch("api.routes.articles.supabase")
def test_get_articles(mock_supabase):
    # Mock return data
    mock_execute = MagicMock()
    mock_execute.execute.return_value.data = [
        {
            "id": "1",
            "title": "Test Article",
            "summary": "This is a test article.",
            "why_it_matters": "It matters because of testing.",
            "source_url": "https://example.com/test",
            "source": "Test Source",
            "score": 5.0,
            "topics": ["Testing"],
            "is_delivered": False,
            "created_at": "2026-06-13T00:00:00Z"
        }
    ]

    mock_supabase.table.return_value.select.return_value.eq.return_value.gte.return_value.order.return_value.range.return_value = mock_execute

    headers = {"X-User-Id": TEST_USER_ID}
    response = client.get("/articles/", headers=headers)
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["title"] == "Test Article"

    # Check that X-User-Id header is required
    response_no_auth = client.get("/articles/")
    assert response_no_auth.status_code == 401

@patch("api.routes.articles.supabase")
def test_submit_feedback(mock_supabase):
    # Mock verify check
    mock_select_res = MagicMock()
    mock_select_res.execute.return_value.data = [{"id": "1"}]

    mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value = mock_select_res

    # Mock insert
    mock_insert_res = MagicMock()
    mock_insert_res.execute.return_value.data = []
    mock_supabase.table.return_value.insert.return_value = mock_insert_res

    headers = {"X-User-Id": TEST_USER_ID}
    response = client.post("/articles/1/feedback", json={"signal": "clicked"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "success"

@patch("api.routes.sources.supabase")
def test_get_sources(mock_supabase):
    mock_select_res = MagicMock()
    mock_select_res.execute.return_value.data = [
        {
            "id": 10,
            "name": "Test Source",
            "url": "https://example.com/feed",
            "is_active": True,
            "created_at": "2026-06-13T00:00:00Z"
        }
    ]
    mock_supabase.table.return_value.select.return_value.eq.return_value = mock_select_res

    headers = {"X-User-Id": TEST_USER_ID}
    response = client.get("/sources/", headers=headers)
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["name"] == "Test Source"

@patch("api.routes.sources.supabase")
def test_add_source(mock_supabase):
    mock_insert_res = MagicMock()
    mock_insert_res.execute.return_value.data = [
        {
            "id": 11,
            "name": "New Source",
            "url": "https://example.com/newfeed",
            "is_active": True,
            "created_at": "2026-06-13T00:00:00Z"
        }
    ]
    mock_supabase.table.return_value.insert.return_value = mock_insert_res

    headers = {"X-User-Id": TEST_USER_ID}
    response = client.post("/sources/", json={"name": "New Source", "url": "https://example.com/newfeed"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["name"] == "New Source"

@patch("api.routes.sources.supabase")
def test_toggle_source(mock_supabase):
    mock_select_res = MagicMock()
    mock_select_res.execute.return_value.data = [{"is_active": True}]
    mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value = mock_select_res

    mock_update_res = MagicMock()
    mock_update_res.execute.return_value.data = []
    mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value = mock_update_res

    headers = {"X-User-Id": TEST_USER_ID}
    response = client.patch("/sources/10/toggle", headers=headers)
    assert response.status_code == 200
    assert response.json()["is_active"] is False

@patch("api.routes.config.supabase")
def test_get_config(mock_supabase):
    mock_select_res = MagicMock()
    mock_select_res.execute.return_value.data = [
        {"value": {"allowed": ["ai"], "blocked": ["crypto"], "priority": ["machine learning"]}}
    ]
    mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value = mock_select_res

    headers = {"X-User-Id": TEST_USER_ID}
    response = client.get("/config/", headers=headers)
    assert response.status_code == 200
    assert response.json()["allowed"] == ["ai"]

@patch("api.routes.config.supabase")
def test_update_config(mock_supabase):
    mock_upsert_res = MagicMock()
    mock_upsert_res.execute.return_value.data = [
        {"value": {"allowed": ["ai"], "blocked": ["crypto"], "priority": ["machine learning"]}}
    ]
    mock_supabase.table.return_value.upsert.return_value = mock_upsert_res

    headers = {"X-User-Id": TEST_USER_ID}
    response = client.put("/config/", json={"allowed": ["ai"], "blocked": ["crypto"], "priority": ["machine learning"]}, headers=headers)
    assert response.status_code == 200
    assert response.json()["config"]["allowed"] == ["ai"]

@patch("api.routes.config.supabase")
def test_get_user_stats(mock_supabase):
    # Mock counts and last delivery
    mock_articles_res = MagicMock()
    mock_articles_res.count = 5

    mock_sources_res = MagicMock()
    mock_sources_res.count = 2

    mock_delivery_res = MagicMock()
    mock_delivery_res.data = [{"created_at": "2026-06-13T01:00:00Z"}]

    mock_supabase.table.return_value.select.return_value.eq.return_value = mock_articles_res
    mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value = mock_sources_res
    mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value = mock_delivery_res

    headers = {"X-User-Id": TEST_USER_ID}
    response = client.get("/config/stats", headers=headers)

    # Since we are mocking table calls, we verify it executes successfully
    assert response.status_code == 200

@patch("api.routes.pipeline.run_all_async")
def test_trigger_pipeline(mock_run_all_async):
    headers = {"X-User-Id": TEST_USER_ID}
    response = client.post("/pipeline/run", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
