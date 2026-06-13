# TechPulse AI User Guide

Welcome to your personalized tech intelligence assistant. This guide explains how to manage your news feeds, configure your topic filters, read digests, and query your catalog using the unified `pulse` CLI and REST API.

---

## 🏗️ How to Manage RSS Feeds

You can manage your news sources using the `pulse feeds` command group. All configurations are stored per-user.

### 1. Add a Single Source
Use the `add` command to register a new feed by its URL and descriptive name:
```bash
uv run pulse feeds add "Python News" "https://www.python.org/blogs/feed/"
```

### 2. List Active Sources
See what RSS feeds you are currently tracking:
```bash
uv run pulse feeds list
```

### 3. Remove a Source
Remove a feed that you no longer need by specifying its database ID (displayed in the feed list):
```bash
uv run pulse feeds remove 12
```

---

## 🎯 How to Configure Topic Filters

Topic filters control what the AI ranks as "relevant" for you, muting noise and boosting critical updates.

### 1. Set Your Interests
Use the `filter set` command to update your filters. Use commas to separate multiple keywords.

*   `--allowed`: General tech topics you want included in your catalog.
*   `--blocked`: Keywords to completely block from ingestion at the collector level.
*   `--priority`: Core interests that receive a significant scoring boost to ensure they surface at the top of your briefings.

```bash
uv run pulse filter set --allowed "ai, Python, security" --blocked "crypto, gaming, automotive" --priority "ai, Python"
```

### 2. View Current Configuration
To inspect your current active filters:
```bash
uv run pulse filter show
```

### 3. Clear Configuration
To reset all filters back to empty lists:
```bash
uv run pulse filter clear
```

---

## 🚀 Basic Getting Started Flow

1.  **Login**: Authenticate with your Supabase credentials:
    ```bash
    uv run pulse login
    ```
2.  **Configure Filters**: Define your technical interests:
    ```bash
    uv run pulse filter set --allowed "Python, LLM, security" --blocked "crypto" --priority "security"
    ```
3.  **Add Feeds**: Add a few technical feeds to scrape:
    ```bash
    uv run pulse feeds add "Hacker News" "https://hnrss.org/frontpage"
    ```
4.  **Check Status**: View pending items in your queue:
    ```bash
    uv run pulse status
    ```
5.  **Read Digest**: Render the latest AI-curated digest directly in your terminal:
    ```bash
    uv run pulse digest
    ```
6.  **Interactive Cited Search (RAG)**: Search your personal knowledge catalog using the REST API:
    ```bash
    curl -X 'POST' \
      'http://localhost:8000/search/rag' \
      -H 'Content-Type: application/json' \
      -H 'x-user-id: <your-user-id>' \
      -d '{
      "query": "What are the latest breakthroughs in AI agents?"
    }'
    ```
