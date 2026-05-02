from services.agents.composer_agent import assign_theme
from services.delivery.main import group_by_themes
from services.collector.filter import is_relevant


def test_assign_theme_generative_ai():
    article = {
        "title": "New LLM breaks all benchmarks",
        "summary": "A new transformer model...",
    }
    assert assign_theme(article) == "Generative AI"


def test_assign_theme_security():
    article = {
        "title": "Critical CVE found in OpenSSL",
        "summary": "A new vulnerability was patched...",
    }
    assert assign_theme(article) == "Security"


def test_assign_theme_fallback():
    article = {"title": "Random cooking tips", "summary": "How to cook pasta..."}
    assert assign_theme(article) == "Quiet Signals"


def test_assign_theme_developer_tools():
    article = {
        "title": "New GitHub SDK released",
        "summary": "The open source API got a major update...",
    }
    assert assign_theme(article) == "Developer Tools"


def test_group_by_themes_groups_correctly():
    articles = [
        {
            "title": "LLM research paper",
            "summary": "A new llm model study.",
            "topics": ["Generative AI", "llm"],
        },
        {
            "title": "Github API update",
            "summary": "New github sdk release.",
            "topics": ["Developer Tools", "sdk"],
        },
        {"title": "Cooking tips", "summary": "How to cook.", "topics": []},
    ]
    grouped = group_by_themes(articles)
    # group_by_themes keys by the first topic string in the list
    assert "Generative AI" in grouped
    assert "Developer Tools" in grouped
    # Articles without topics fall back to "General Tech"
    assert "General Tech" in grouped


def test_is_relevant_allows_matching_topic(mocker):
    mocker.patch(
        "services.collector.filter.get_cached_config",
        return_value={"allowed": ["ai", "python"], "blocked": []},
    )
    assert is_relevant("Top AI Breakthroughs") is True
    assert is_relevant("Best Python tutorials") is True


def test_is_relevant_blocks_keyword(mocker):
    mocker.patch(
        "services.collector.filter.get_cached_config",
        return_value={"allowed": ["ai"], "blocked": ["crypto"]},
    )
    assert is_relevant("Crypto price spike") is False


def test_is_relevant_allows_unmatched_v2(mocker):
    """In V2, unmatched topics are allowed through for semantic evaluation by the Ranker."""
    mocker.patch(
        "services.collector.filter.get_cached_config",
        return_value={"allowed": ["ai", "python"], "blocked": []},
    )
    assert is_relevant("Random cooking article") is True


def test_is_relevant_allows_all_when_no_topics(mocker):
    """New users with no allowed topics should get all content (permissive mode)."""
    mocker.patch(
        "services.collector.filter.get_cached_config",
        return_value={"allowed": [], "blocked": []},
    )
    assert is_relevant("Any random tech article") is True
