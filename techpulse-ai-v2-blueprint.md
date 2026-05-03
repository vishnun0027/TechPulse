# TechPulse AI V2: Full Implementation Blueprint
### Zero-Effort, High-Signal Tech Awareness

---

## Executive Summary

TechPulse AI V2 transforms the existing pipeline from an automated news digest into a **personal tech intelligence system**. The upgrade introduces semantic memory, novelty detection, agentic curation, and narrative composition — so every briefing answers three questions: *What changed? Why does it matter? Should you care?* This document provides every schema change, service module, scoring formula, and UI screen needed to implement V2 across both `techpulse-ai` and `techpulse-web`.

The architecture preserves the existing collector → Redis stream → summarizer → delivery skeleton and evolves it into five stages: **Collect → Enrich → Decide → Compose → Deliver**, each with a defined responsibility, data contract, and implementation path.

---

## Part 1: Architecture Overview

### V1 vs V2 Pipeline

```
V1 (current):
  Collector ──XADD──► Redis Stream ──XREADGROUP──► Summarizer ──upsert──► Supabase ──► Delivery ──webhook──► Slack/Discord

V2 (target):
  Collector ──XADD──► Redis Stream
                           │
                     ┌─────▼──────┐
                     │  Enricher  │  ← embed, deduplicate, cluster, novelty score
                     └─────┬──────┘
                           │
                     ┌─────▼──────┐
                     │  Ranker    │  ← personalized relevance score per user
                     └─────┬──────┘
                           │
                     ┌─────▼──────────┐
                     │ Research Agent │  ← LangGraph: retrieve history + web context
                     └─────┬──────────┘
                           │
                     ┌─────▼──────┐
                     │  Supabase  │  ← articles + embeddings + events + feedback
                     └─────┬──────┘
                           │
                     ┌─────▼──────────┐
                     │ Composer Agent │  ← narrative digest with sections
                     └─────┬──────────┘
                           │
                     Slack / Discord / Dashboard
```

### Service Responsibilities

| Service | V1 Role | V2 Role |
|---|---|---|
| `collector` | RSS scrape, dedup by URL/title, keyword pre-filter | Same + source quality scoring |
| `enricher` *(new)* | — | Embed, semantic dedup, event clustering, novelty scoring |
| `ranker` *(new)* | — | Personalized score from profile + feedback signals |
| `summarizer` | Single Groq LLM call | RAG-enhanced summary with "why it matters" |
| `research_agent` *(new)* | — | LangGraph node: retrieve history + optional web search |
| `composer` *(new)* | — | Narrative digest grouped into themed sections |
| `delivery` | Webhook to Slack/Discord | Same + urgency gating + send/no-send decision |

---

## Part 2: Database Schema Changes

### 2.1 Enable pgvector with HNSW Index

HNSW indexes deliver significantly higher query-per-second throughput over IVFFlat — benchmarks show 40.5 QPS vs 2.6 QPS at 99.8% recall — making them the correct choice for real-time retrieval at the scale TechPulse AI will reach.

```sql
-- migrations/v2_001_enable_vector.sql

CREATE EXTENSION IF NOT EXISTS vector;

-- Add embedding column to articles table
ALTER TABLE articles
  ADD COLUMN IF NOT EXISTS embedding vector(768),
  ADD COLUMN IF NOT EXISTS novelty_score    float   DEFAULT 1.0,
  ADD COLUMN IF NOT EXISTS event_id         uuid,
  ADD COLUMN IF NOT EXISTS why_it_matters   text,
  ADD COLUMN IF NOT EXISTS source_quality   float   DEFAULT 0.5,
  ADD COLUMN IF NOT EXISTS v2_processed     boolean DEFAULT false;

-- HNSW index: faster lookup, supports live updates without rebuild
CREATE INDEX IF NOT EXISTS articles_embedding_hnsw
  ON articles
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
```

### 2.2 Article Events Table

Groups multiple articles that cover the same underlying story into one event, eliminating the "same story, five sources" problem.

```sql
-- migrations/v2_002_events.sql

CREATE TABLE IF NOT EXISTS article_events (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  title       text NOT NULL,             -- LLM-generated event title
  theme       text,                      -- e.g. "Generative AI", "Regulation"
  first_seen  timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  article_count int DEFAULT 1,
  centroid_embedding vector(768),        -- mean of all member article embeddings
  CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES auth.users(id)
);

-- RLS: users only see their own events
ALTER TABLE article_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY "user_events" ON article_events
  FOR ALL USING (auth.uid() = user_id);

-- Link articles to events
ALTER TABLE articles
  ADD CONSTRAINT fk_event
  FOREIGN KEY (event_id) REFERENCES article_events(id) ON DELETE SET NULL;
```

### 2.3 User Feedback Table

Captures behavioral signals (clicked, saved, dismissed) that power the personalized ranker. This replaces static topic keywords as the primary relevance signal over time.

```sql
-- migrations/v2_003_feedback.sql

CREATE TABLE IF NOT EXISTS user_feedback (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  article_id  uuid NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
  signal      text NOT NULL CHECK (signal IN (
                  'clicked','saved','dismissed','more_like_this','less_like_this'
               )),
  created_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE user_feedback ENABLE ROW LEVEL SECURITY;
CREATE POLICY "user_feedback_policy" ON user_feedback
  FOR ALL USING (auth.uid() = user_id);

CREATE INDEX idx_feedback_user_signal ON user_feedback(user_id, signal);
```

### 2.4 Source Health Table

Tracks per-source signal quality automatically, so low-value feeds are downranked without manual intervention.

```sql
-- migrations/v2_004_source_health.sql

CREATE TABLE IF NOT EXISTS source_health (
  source_id         uuid NOT NULL REFERENCES rss_sources(id) ON DELETE CASCADE,
  user_id           uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  articles_ingested int  DEFAULT 0,
  articles_delivered int DEFAULT 0,
  articles_clicked  int  DEFAULT 0,
  duplicate_rate    float DEFAULT 0.0,   -- fraction of near-duplicates produced
  quality_score     float DEFAULT 0.5,   -- derived: clicked / delivered
  updated_at      timestamptz DEFAULT now(),
  PRIMARY KEY (source_id, user_id)
);
```

### 2.5 RAG Retrieval Functions

```sql
-- migrations/v2_005_rpc_functions.sql

-- Semantic similarity search scoped per user
CREATE OR REPLACE FUNCTION match_articles(
  query_embedding  vector(768),
  match_threshold  float,
  match_count      int,
  p_user_id        uuid
)
RETURNS TABLE (
  id              uuid,
  title           text,
  summary         text,
  why_it_matters  text,
  published_at    timestamptz,
  similarity      float
)
LANGUAGE sql STABLE AS $$
  SELECT
    id, title, summary, why_it_matters, published_at,
    1 - (embedding <=> query_embedding) AS similarity
  FROM articles
  WHERE user_id       = p_user_id
    AND embedding     IS NOT NULL
    AND 1 - (embedding <=> query_embedding) > match_threshold
  ORDER BY embedding <=> query_embedding
  LIMIT match_count;
$$;

-- Near-duplicate check: returns true if a very similar article exists
CREATE OR REPLACE FUNCTION is_near_duplicate(
  query_embedding  vector(768),
  dup_threshold    float,
  p_user_id        uuid
)
RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT EXISTS (
    SELECT 1 FROM articles
    WHERE user_id = p_user_id
      AND embedding IS NOT NULL
      AND 1 - (embedding <=> query_embedding) >= dup_threshold
  );
$$;

-- Recency-weighted similarity (fuses content similarity with freshness)
-- novelty = similarity_score * exp(-decay_rate * days_since_publication)
CREATE OR REPLACE FUNCTION match_articles_recency(
  query_embedding  vector(768),
  match_count      int,
  p_user_id        uuid,
  decay_rate       float DEFAULT 0.1
)
RETURNS TABLE (id uuid, title text, summary text, recency_score float)
LANGUAGE sql STABLE AS $$
  SELECT
    id, title, summary,
    (1 - (embedding <=> query_embedding))
      * exp(-decay_rate * EXTRACT(EPOCH FROM (now() - published_at)) / 86400)
    AS recency_score
  FROM articles
  WHERE user_id = p_user_id
    AND embedding IS NOT NULL
  ORDER BY recency_score DESC
  LIMIT match_count;
$$;
```

---

## Part 3: Python Backend — New Service Modules

### 3.1 Project Structure Changes

```
src/
├── services/
│   ├── collector/        (existing — minor changes)
│   ├── enricher/         ← NEW
│   │   ├── __init__.py
│   │   ├── embedder.py
│   │   ├── deduplicator.py
│   │   ├── clusterer.py
│   │   └── novelty.py
│   ├── ranker/           ← NEW
│   │   ├── __init__.py
│   │   └── scorer.py
│   ├── summarizer/       (existing — enhanced)
│   │   ├── __init__.py
│   │   ├── consumer.py
│   │   └── rag_summarizer.py   ← MODIFIED
│   ├── agents/           ← NEW
│   │   ├── __init__.py
│   │   ├── research_agent.py
│   │   └── composer_agent.py
│   └── delivery/         (existing — enhanced)
├── shared/               (existing)
```

### 3.2 Enricher Service

**`src/services/enricher/embedder.py`**

Uses Groq's `nomic-embed-text-v1_5` so there is no local model download — same API key, zero cold-start overhead, 768-dimensional output.

```python
from groq import Groq
from loguru import logger

_client: Groq | None = None

def get_client(api_key: str) -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=api_key)
    return _client

def embed_text(text: str, api_key: str) -> list[float]:
    """Returns a 768-dim embedding via Groq nomic-embed-text-v1_5."""
    client = get_client(api_key)
    truncated = text[:8192]  # model token limit
    try:
        response = client.embeddings.create(
            model="nomic-embed-text-v1_5",
            input=truncated
        )
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"Embedding failed: {e}")
        raise
```

**`src/services/enricher/deduplicator.py`**

```python
from supabase import Client
from loguru import logger

NEAR_DUPLICATE_THRESHOLD = 0.92  # cosine similarity above this = same story

def is_near_duplicate(
    supabase: Client,
    embedding: list[float],
    user_id: str
) -> bool:
    """Returns True if a semantically near-identical article already exists."""
    result = supabase.rpc("is_near_duplicate", {
        "query_embedding": embedding,
        "dup_threshold": NEAR_DUPLICATE_THRESHOLD,
        "p_user_id": user_id
    }).execute()
    return result.data
```

**`src/services/enricher/novelty.py`**

Implements RAG-Novelty: an article is more novel if it retrieves fewer recently published similar articles from the archive. This approach is validated in published research on novelty assessment using retrieval-augmented scoring.

```python
from supabase import Client
from datetime import datetime, timezone

def compute_novelty_score(
    supabase: Client,
    embedding: list[float],
    user_id: str,
    match_count: int = 5
) -> float:
    """
    Novelty score 0.0–1.0.
    High novelty = few similar articles in recent history.
    Low novelty  = many highly similar articles already seen.
    """
    result = supabase.rpc("match_articles_recency", {
        "query_embedding": embedding,
        "match_count": match_count,
        "p_user_id": user_id,
        "decay_rate": 0.15
    }).execute()

    similar_items = result.data or []
    if not similar_items:
        return 1.0  # nothing similar found — fully novel

    # Average recency-weighted similarity of top matches
    avg_similarity = sum(r["recency_score"] for r in similar_items) / len(similar_items)

    # Invert: high similarity → low novelty
    novelty = max(0.0, 1.0 - avg_similarity)
    return round(novelty, 4)
```

**`src/services/enricher/clusterer.py`**

```python
from supabase import Client
from groq import Groq
import uuid

CLUSTER_THRESHOLD = 0.85  # articles above this similarity join same event

def find_or_create_event(
    supabase: Client,
    groq_client: Groq,
    embedding: list[float],
    article_title: str,
    user_id: str
) -> str:
    """
    Finds an existing article_event with a similar centroid,
    or creates a new one. Returns the event_id.
    """
    # Find existing event with similar centroid embedding
    result = supabase.rpc("match_events_by_centroid", {
        "query_embedding": embedding,
        "threshold": CLUSTER_THRESHOLD,
        "p_user_id": user_id
    }).execute()

    if result.data:
        event_id = result.data[0]["id"]
        # Update centroid and count
        supabase.table("article_events").update({
            "article_count": result.data[0]["article_count"] + 1,
            "updated_at": "now()"
        }).eq("id", event_id).execute()
        return event_id

    # Create new event — generate a clean event title via LLM
    title_response = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{
            "role": "user",
            "content": f"In 8 words or fewer, name the tech story: '{article_title}'"
        }],
        max_tokens=20
    )
    event_title = title_response.choices[0].message.content.strip().strip('"')

    new_event = supabase.table("article_events").insert({
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "title": event_title,
        "centroid_embedding": embedding,
        "article_count": 1
    }).execute()

    return new_event.data[0]["id"]
```

### 3.3 Ranker Service

**`src/services/ranker/scorer.py`**

Combines five signals into one ranked score. The formula is additive and inspectable — each weight can be tuned per user in `app_config`.

```python
from supabase import Client
from dataclasses import dataclass

@dataclass
class RankSignals:
    base_relevance:    float  # 0–5: LLM relevance score from current summarizer
    novelty_score:     float  # 0–1: from enricher/novelty.py
    source_quality:    float  # 0–1: from source_health table
    topic_match:       float  # 0–1: keyword profile match (existing logic)
    priority_boost:    float  # +1.0 if matches priority topics (existing logic)

# Default weights — can be stored per user in app_config
DEFAULT_WEIGHTS = {
    "base_relevance": 0.35,
    "novelty_score":  0.25,
    "source_quality": 0.20,
    "topic_match":    0.15,
    "priority_boost": 0.05,
}

def compute_final_score(signals: RankSignals, weights: dict = DEFAULT_WEIGHTS) -> float:
    """
    Returns a final score 0.0–10.0.
    Articles scoring below DELIVERY_THRESHOLD are excluded from digests.
    """
    score = (
        signals.base_relevance   * weights["base_relevance"] * 2.0 +  # normalize 0–5 to 0–1
        signals.novelty_score    * weights["novelty_score"]  * 10.0 +
        signals.source_quality   * weights["source_quality"] * 10.0 +
        signals.topic_match      * weights["topic_match"]    * 10.0 +
        signals.priority_boost   * weights["priority_boost"] * 10.0
    )
    return round(min(score, 10.0), 4)

DELIVERY_THRESHOLD = 4.5   # articles below this are stored but not delivered
BREAKING_THRESHOLD = 8.0   # articles above this trigger an immediate alert
```

### 3.4 Research Agent (LangGraph)

LangGraph 2.0 has emerged as the dominant orchestration layer for production agentic systems, with directed cyclic graphs that support conditional branching, retries, and parallel execution — exactly the pattern needed to enrich articles before summarization.

**`src/services/agents/research_agent.py`**

```python
from langgraph.graph import StateGraph, END
from langchain_groq import ChatGroq
from typing import TypedDict, Annotated
from supabase import Client
import operator

class ResearchState(TypedDict):
    article_text:   str
    article_title:  str
    user_id:        str
    embedding:      list[float]
    similar_history: list[dict]
    web_context:    str
    final_summary:  str
    why_it_matters: str

def retrieve_history(state: ResearchState, supabase: Client) -> ResearchState:
    """Node 1: Pull top-3 related articles from Supabase pgvector."""
    from services.enricher.embedder import embed_text
    result = supabase.rpc("match_articles", {
        "query_embedding": state["embedding"],
        "match_threshold": 0.72,
        "match_count": 3,
        "p_user_id": state["user_id"]
    }).execute()
    state["similar_history"] = result.data or []
    return state

def build_summary(state: ResearchState, groq_api_key: str) -> ResearchState:
    """Node 2: RAG-enhanced summarization with historical context."""
    from langchain_groq import ChatGroq

    llm = ChatGroq(model="llama-3.3-70b-versatile", api_key=groq_api_key)

    history_context = ""
    if state["similar_history"]:
        history_context = "\n".join([
            f"- [{r['published_at'][:10]}] {r['title']}: {r.get('why_it_matters', r['summary'][:120])}"
            for r in state["similar_history"]
        ])

    prompt = f"""You are a precise tech analyst. Summarize the article below in 3-4 sentences.
Then in one sentence, explain WHY this matters to a developer or tech professional.
If historical context is provided, note what specifically changed or is new.

HISTORICAL CONTEXT (related past coverage):
{history_context or "No prior coverage found — this appears to be a new story."}

ARTICLE:
{state["article_text"][:4000]}

Respond in this exact JSON format:
{{
  "summary": "...",
  "why_it_matters": "..."
}}"""

    response = llm.invoke(prompt)
    import json
    try:
        parsed = json.loads(response.content)
        state["final_summary"]  = parsed["summary"]
        state["why_it_matters"] = parsed["why_it_matters"]
    except Exception:
        state["final_summary"]  = response.content
        state["why_it_matters"] = ""
    return state

def build_research_agent(supabase: Client, groq_api_key: str):
    """Constructs and compiles the LangGraph research agent."""
    graph = StateGraph(ResearchState)

    graph.add_node("retrieve_history", lambda s: retrieve_history(s, supabase))
    graph.add_node("build_summary",    lambda s: build_summary(s, groq_api_key))

    graph.set_entry_point("retrieve_history")
    graph.add_edge("retrieve_history", "build_summary")
    graph.add_edge("build_summary", END)

    return graph.compile()
```

### 3.5 Composer Agent

**`src/services/agents/composer_agent.py`**

```python
from groq import Groq
from supabase import Client
from loguru import logger
from ranker.scorer import DELIVERY_THRESHOLD, BREAKING_THRESHOLD

SECTION_THEMES = {
    "🧠 Generative AI":    ["llm", "gpt", "claude", "gemini", "llama", "transformer", "fine-tuning"],
    "🔧 Developer Tools":  ["api", "sdk", "framework", "library", "release", "open source", "github"],
    "🏢 Industry":         ["funding", "acquisition", "startup", "ipo", "layoffs", "valuation"],
    "🔒 Security":         ["vulnerability", "breach", "cve", "exploit", "patch", "malware"],
    "📜 Regulation":       ["regulation", "policy", "gdpr", "ban", "law", "government", "compliance"],
    "🔬 Research":         ["paper", "arxiv", "benchmark", "study", "dataset", "model"],
    "📡 Quiet Signals":    [],  # catch-all for low-score but novel items
}

def assign_theme(article: dict) -> str:
    text = (article.get("title", "") + " " + article.get("summary", "")).lower()
    for theme, keywords in SECTION_THEMES.items():
        if keywords and any(kw in text for kw in keywords):
            return theme
    return "📡 Quiet Signals"

def compose_digest(
    supabase: Client,
    groq_client: Groq,
    user_id: str,
    top_n: int = 12
) -> dict:
    """
    Fetches top undelivered articles for a user, groups them
    into thematic sections, and generates a narrative intro.
    Returns a structured digest dict.
    """
    # Fetch top-ranked, undelivered articles
    result = supabase.table("articles") \
        .select("id, title, summary, why_it_matters, url, final_score, novelty_score, published_at") \
        .eq("user_id", user_id) \
        .eq("delivered", False) \
        .gte("final_score", DELIVERY_THRESHOLD) \
        .order("final_score", desc=True) \
        .limit(top_n) \
        .execute()

    articles = result.data or []
    if not articles:
        return {"empty": True}

    # Group into themes
    sections: dict[str, list] = {theme: [] for theme in SECTION_THEMES}
    for article in articles:
        theme = assign_theme(article)
        sections[theme].append(article)

    # Remove empty sections
    sections = {k: v for k, v in sections.items() if v}

    # Generate digest narrative intro via LLM
    article_titles = "\n".join([f"- {a['title']}" for a in articles[:8]])
    intro_response = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{
            "role": "user",
            "content": f"""Write a 2-sentence tech briefing intro for these stories.
Be direct, no fluff. Start with the most important theme.
Stories:\n{article_titles}"""
        }],
        max_tokens=80
    )
    intro = intro_response.choices[0].message.content.strip()

    # Check for breaking news
    breaking = [a for a in articles if a.get("final_score", 0) >= BREAKING_THRESHOLD]

    return {
        "empty":    False,
        "intro":    intro,
        "breaking": breaking,
        "sections": sections,
        "total":    len(articles),
        "user_id":  user_id
    }
```

### 3.6 Updated `techpulse-ops run all` Sequence

The updated pipeline coordinator calls each new stage in order, treating the whole run as one transaction per article:

```python
# src/cli/ops.py — updated run_all() function

async def run_all():
    logger.info("TechPulse AI V2 pipeline started")

    # Stage 1: Collect
    await collector.run()

    # Stage 2: Enrich (embed + dedup + cluster + novelty)
    async for article in stream_consumer.read():
        embedding = embedder.embed_text(article["text"], GROQ_API_KEY)

        # Skip near-duplicates entirely
        if deduplicator.is_near_duplicate(supabase, embedding, article["user_id"]):
            logger.info(f"SKIP near-duplicate: {article['title'][:60]}")
            stream_consumer.ack(article["id"])
            continue

        novelty = novelty.compute_novelty_score(supabase, embedding, article["user_id"])
        event_id = clusterer.find_or_create_event(supabase, groq, embedding, article["title"], article["user_id"])

        # Stage 3: Rank
        signals = scorer.RankSignals(
            base_relevance=article["relevance_score"],
            novelty_score=novelty,
            source_quality=source_health.get_quality(article["source_id"], article["user_id"]),
            topic_match=keyword_matcher.score(article, article["user_id"]),
            priority_boost=1.0 if keyword_matcher.is_priority(article, article["user_id"]) else 0.0
        )
        final_score = scorer.compute_final_score(signals)

        # Stage 4: Research Agent (RAG summarization)
        agent = build_research_agent(supabase, GROQ_API_KEY)
        result = agent.invoke({
            "article_text":  article["text"],
            "article_title": article["title"],
            "user_id":       article["user_id"],
            "embedding":     embedding
        })

        # Stage 5: Upsert enriched article to Supabase
        supabase.table("articles").upsert({
            **article,
            "embedding":       embedding,
            "novelty_score":   novelty,
            "event_id":        event_id,
            "final_score":     final_score,
            "summary":         result["final_summary"],
            "why_it_matters":  result["why_it_matters"],
            "v2_processed":    True
        }).execute()

        stream_consumer.ack(article["id"])

    # Stage 6: Compose + Deliver
    for user_id in get_active_users():
        digest = composer_agent.compose_digest(supabase, groq, user_id)
        if not digest["empty"]:
            delivery.send_digest(user_id, digest)

    logger.info("TechPulse AI V2 pipeline complete")
```

### 3.7 Updated `pyproject.toml` Dependencies

```toml
[project]
dependencies = [
    # existing
    "groq>=0.11.0",
    "supabase>=2.7.0",
    "redis>=5.0.0",
    "loguru>=0.7.0",
    "pydantic>=2.7.0",
    "feedparser>=6.0.0",
    # new in V2
    "langgraph>=0.2.0",
    "langchain-groq>=0.1.0",
]
```

---

## Part 4: Frontend Changes (techpulse-web)

### 4.1 New Routes

Add three new routes to `App.jsx`:

```jsx
<Route path="/brief"   element={<MorningBriefView  session={session} />} />
<Route path="/search"  element={<SemanticSearchView session={session} />} />
<Route path="/radar"   element={<RadarView          session={session} />} />
```

### 4.2 Morning Brief View

**Purpose:** Replace the flat article list with a narrative digest. Each section maps to a theme from the Composer Agent.

**Key UI elements:**
- Digest intro paragraph (LLM-generated, shown at top)
- 🚨 Breaking section (if any articles score ≥ 8.0) shown above fold
- Themed section cards: theme emoji + title + article list
- Each article shows: Title, `why_it_matters` one-liner, novelty indicator, source name, time
- Feedback buttons on each article: 👍 (save), 👎 (dismiss), ➕ (more like this)

**Feedback handler (writes to `user_feedback` table):**
```jsx
async function handleFeedback(articleId, signal) {
  await supabase.from('user_feedback').insert({
    article_id: articleId,
    signal: signal   // 'saved' | 'dismissed' | 'more_like_this' | 'less_like_this'
  });
}
```

### 4.3 Semantic Search View

**Purpose:** Let users query their entire article archive by meaning, not keywords.

**Supabase RPC call from the frontend:**
```jsx
async function semanticSearch(queryText) {
  // Step 1: Get embedding for query via Edge Function
  const { data: embeddingData } = await supabase.functions.invoke('embed', {
    body: { text: queryText }
  });

  // Step 2: Similarity search
  const { data } = await supabase.rpc('match_articles', {
    query_embedding:  embeddingData.embedding,
    match_threshold:  0.65,
    match_count:      10,
    p_user_id:        session.user.id
  });

  return data;
}
```

**UI:** Search bar at top, results appear as cards with a similarity percentage badge, ordered by semantic relevance. Each result shows title, `why_it_matters`, date, and source.

### 4.4 Radar View

**Purpose:** Surface trends, recurring entities, and "quiet but growing" topics across the last 30 days.

**Panels to include:**
- **Top Events** — most-covered stories grouped by `article_events`, with article count and timeline
- **Rising Topics** — topics that appear more in the last 7 days vs the prior 7 days
- **Source Leaderboard** — ranked by `source_health.quality_score` (click rate / delivery rate)
- **Novel vs Repeat ratio** — simple bar chart showing the fraction of daily articles that were genuinely novel vs near-duplicates suppressed

**Data query:**
```jsx
// Fetch event clusters from last 30 days
const { data: events } = await supabase
  .from('article_events')
  .select('title, article_count, theme, first_seen, updated_at')
  .eq('user_id', session.user.id)
  .gte('updated_at', thirtyDaysAgo)
  .order('article_count', { ascending: false })
  .limit(20);
```

### 4.5 Updated DashboardLayout Navigation

Add the three new views to the sidebar:

```jsx
const navItems = [
  { path: '/',       icon: <LayoutDashboard />, label: 'Feed'           },
  { path: '/brief',  icon: <Newspaper />,       label: 'Morning Brief'  },  // NEW
  { path: '/search', icon: <Search />,           label: 'Ask TechPulse' },  // NEW
  { path: '/radar',  icon: <Activity />,         label: 'Radar'         },  // NEW
  { path: '/settings', icon: <Settings />,       label: 'Settings'      },
];
```

---

## Part 5: Deployment on Render (Free Tier)

Since the full pipeline runs as a Cron Job — no persistent worker required — Groq handles both embedding and LLM summarization via API (zero local model memory), and Supabase stores vectors natively, the entire V2 system runs on Render's free tier.

### Render Services

| Service | Type | Cost | Schedule |
|---|---|---|---|
| `techpulse-web` | Static Site | Free | — |
| `techpulse-ai` full pipeline | Cron Job | Free | `0 */6 * * *` |

### Cron Job Configuration

| Setting | Value |
|---|---|
| Build Command | `pip install uv && uv sync` |
| Command | `uv run techpulse-ops run all` |
| Schedule | `0 */6 * * *` (every 6 hours) |

### Environment Variables

```
SUPABASE_URL
SUPABASE_KEY
SUPABASE_ANON_KEY
GROQ_API_KEY          ← used for both embeddings and LLM
UPSTASH_REDIS_REST_URL
UPSTASH_REDIS_REST_TOKEN
COLLECTION_INTERVAL_DAYS = 14
VITE_SUPABASE_URL     ← frontend only
VITE_SUPABASE_ANON_KEY
VITE_ADMIN_EMAIL
```

---

## Part 6: Phased Implementation Roadmap

### Phase 1: Signal Quality (Week 1–2)
- [ ] Run migrations v2_001 through v2_005
- [ ] Add `enricher/` module (embedder, deduplicator, novelty)
- [ ] Add `ranker/scorer.py`
- [ ] Update `run_all()` to call enricher + ranker before summarizer
- [ ] Add `why_it_matters` field to existing summary prompt (no LangGraph yet)
- [ ] Verify near-duplicate suppression is working in logs

### Phase 2: Memory and RAG (Week 3–4)
- [ ] Integrate `research_agent.py` (LangGraph)
- [ ] Replace existing single Groq call in summarizer with agent invocation
- [ ] Add `composer_agent.py` and update delivery to use structured digest
- [ ] Deploy `Morning Brief` view in `techpulse-web`
- [ ] Add feedback buttons and `user_feedback` writes

### Phase 3: Search, Radar, and Learning (Week 5–6)
- [ ] Deploy Supabase Edge Function for client-side embedding (for semantic search)
- [ ] Build `SemanticSearchView` and `RadarView` in `techpulse-web`
- [ ] Weekly batch job to update `source_health.quality_score` from feedback signals
- [ ] Expose user-adjustable ranking weights in `SettingsView`
- [ ] Monitor digest open rate and novelty ratio to tune `DELIVERY_THRESHOLD`

---

## Scoring Formula Reference

The final ranking score is computed as:

\[ S = 2w_1 \cdot \frac{r}{5} + 10w_2 \cdot n + 10w_3 \cdot q + 10w_4 \cdot t + 10w_5 \cdot p \]

where:
- \(r\) = LLM relevance score (0–5), \(n\) = novelty score (0–1)
- \(q\) = source quality (0–1), \(t\) = topic match (0–1), \(p\) = priority boost (0 or 1)
- Default weights: \(w_1=0.35,\ w_2=0.25,\ w_3=0.20,\ w_4=0.15,\ w_5=0.05\)
- Delivery threshold: \(S \geq 4.5\); Breaking threshold: \(S \geq 8.0\)

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| Groq for embeddings (not sentence-transformers) | Zero cold-start overhead on Render Cron Jobs; same API key already in use |
| HNSW over IVFFlat index | 40.5 QPS vs 2.6 QPS at equivalent recall; supports live inserts without rebuild |
| Additive weighted scoring | Inspectable, tunable per user; each signal's contribution is visible |
| LangGraph for research agent | Production-grade stateful orchestration with conditional branching and retry support |
| Event clustering in DB | Suppresses "same story, five sources" at storage time rather than display time |
| Feedback table over implicit signals | Explicit signals (saved, dismissed) are more reliable than open-rate proxies at small scale |
| Render free tier (Cron Job only) | Full V2 pipeline runs in a single cron execution; no always-on worker needed |


---

## Part 7: Project Status (2026-04-22)

The TechPulse AI V2 backend engine is now **feature-complete and aligned**.

### ✅ Completed (Backend Engine)
- **Signal Quality**: Full enrichment pipeline (embeddings, deduplication, novelty, clustering) integrated into `techpulse-ops`.
- **Memory & RAG**: LangGraph Research Agent fully operational with historical context retrieval and "Why It Matters" analysis.
- **Narrative Delivery**: Composer Agent functional; delivers themed narrative digests instead of flat article lists.
- **Data Persistence**: Consolidated 11+ migrations into a single [master_schema_v2.sql](file:///home/vishnu/worklab/techpulse-ai/migrations/master_schema_v2.sql).
- **Source Health**: Real-time telemetry tracking (ingestion counts, click rates) hooked into the ranking algorithm.
- **Reliability Hardening**:
    - Resolved consumer group race conditions (started using ID '0' for fresh starts).
    - Fixed maintenance module (`reset --confirm`) for clean system restarts.
    - Standardized data models in `src/shared/models.py`.

### ⏳ Pending (Implementation Gap)
- **UI 2.0 (web)**: The `techpulse-web` frontend repository is currently missing from this workspace. The following routes and views need implementation:
    - `/brief`: Narrative morning briefing view.
    - `/search`: Semantic search interface with Supabase RPC integration.
    - `/radar`: Trend visualization and source quality dashboard.
- **Edge Deployment**: Deployment of Supabase Edge Functions for client-side query embedding (required for search).
- **Infrastructure**: Update NVIDIA GPU drivers (currently version 12060 forces CPU usage; functional but slower).
- **Feedback Loop**: Backend batch job for processing signals implemented; Frontend buttons for 👍/👎 need to be wired to the `user_feedback` table.

---
