# TechPulse AI — Complete Dataflow & Logic Reference

> Every detail in this document is traced directly to source code in `src/`.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Infrastructure & Storage Layers](#2-infrastructure--storage-layers)
3. [Multi-Tenancy & RBAC](#3-multi-tenancy--rbac)
4. [Stage 1 — Collector](#4-stage-1--collector)
5. [Stage 2 — Enricher](#5-stage-2--enricher)
6. [Stage 3 — Ranker](#6-stage-3--ranker)
7. [Stage 3.5 — Early Rejection Gate](#7-stage-35--early-rejection-gate)
8. [Stage 4 — Research Agent (RAG)](#8-stage-4--research-agent)
9. [Stage 5 — Persist to DB](#9-stage-5--persist-to-db)
10. [Stage 6 — Composer Agent](#10-stage-6--composer-agent)
11. [Stage 7 — Delivery Service](#11-stage-7--delivery-service)
12. [Parallel Path — Legacy Summarizer](#12-parallel-path--legacy-summarizer)
13. [Feedback Loop](#13-feedback-loop)
14. [Redis Stream Mechanics](#14-redis-stream-mechanics)
15. [Key Data Structures](#15-key-data-structures)
16. [Configuration & Thresholds](#16-configuration--thresholds)

---

## 1. System Overview

TechPulse AI answers one question per article:

> *"Tell me only what changed, why it matters, and what I should watch next."*

The pipeline is designed around **cost-gating**: cheap checks happen first, expensive AI calls only happen after an article has survived all earlier filters.

```
RSS Feeds
  │
  ▼
[Stage 1]  Collector       — fetch, browser-mimic, freshness, Redis dedup, topic block
  │                           → push to Redis Stream "stream:raw"
  ▼
[Stage 2]  Enricher        — embed (768-dim), semantic dedup (pgvector), novelty score, event cluster
  │
  ▼
[Stage 3]  Ranker          — weighted multi-signal score 0–10
  │
  ▼
[Stage 3.5] Rejection Gate — drop if score < delivery_threshold (3.5)
  │
  ▼
[Stage 4]  Research Agent  — LangGraph RAG: retrieve 3 related articles → LLM summary + why_it_matters
  │
  ▼
[Stage 5]  Persist         — upsert to Supabase articles table
  │
  ▼
[Stage 6]  Composer        — group by theme, LLM narrative intro, flag breaking news (score ≥ 8.0)
  │
  ▼
[Stage 7]  Delivery        — Slack Block Kit + Discord Markdown chunks, mark delivered, update source_health
  │
  ▼
[Async]    Feedback Loop   — aggregate user_feedback → recompute source quality scores
```

---

## 2. Infrastructure & Storage Layers

| Layer | Technology | Purpose |
|---|---|---|
| Article DB | Supabase (PostgreSQL) | Primary store: articles, tenants, sources, telemetry |
| Vector Index | pgvector HNSW on Supabase | Semantic dedup, novelty, RAG retrieval |
| Pipeline Queue | Upstash Redis REST | `stream:raw` Redis Stream — decouples Collector from Enricher |
| URL/Title Dedup Cache | Upstash Redis REST | `seen:{user_id}:{md5}` and `title:{user_id}:{slug}` keys with TTL |
| AI Inference | Groq API | `llama-3.1-8b-instant` (summarizer), `llama-3.3-70b-versatile` (research agent) |
| Embeddings | Sentence-Transformers (local) | `all-mpnet-base-v2` → 768-dim vectors |

### Supabase Tables

| Table | What it holds |
|---|---|
| `articles` | title, summary, why_it_matters, score, topics, embedding, is_delivered, v2_processed, source_id, novelty_score, event_id |
| `rss_sources` | Per-user RSS feed URLs, is_active flag |
| `tenant_profiles` | user_id, email, role, slack_webhook_url, discord_webhook_url, full_name, is_admin |
| `app_config` | Per-user JSON blob stored under key `"topics"`: `{allowed:[...], blocked:[...], priority:[...]}` |
| `source_health` | Per (source_id, user_id): articles_delivered, articles_clicked, quality_score |
| `article_events` | Event clusters: centroid_embedding, article_count, updated_at |
| `telemetry` | service, metric_name, value, success per pipeline run |
| `user_feedback` | article_id, user_id, signal (clicked/saved/dismissed/more_like_this/less_like_this) |

### Supabase RPCs (PostgreSQL functions called via `.rpc()`)

| RPC | Called by | What it does |
|---|---|---|
| `is_near_duplicate` | Deduplicator | Returns bool — cosine sim > threshold vs all user articles |
| `match_articles_recency` | Novelty scorer | Top-N similar articles with exponential time-decay weighting |
| `match_events_by_centroid` | Clusterer | Finds closest event cluster centroid above threshold |
| `match_articles` | Research Agent | Top-3 related articles for RAG context (threshold 0.72) |
| `increment_source_ingestion` | Collector | Atomic increment of source_health ingestion counters |

---

## 3. Multi-Tenancy & RBAC

### 4-Role System

| Role | Pipeline behavior |
|---|---|
| `admin` | Excluded from automated delivery. Cross-tenant visibility in CLI/dashboard. |
| `auditor` | Observer only — not delivered to. |
| `premium` | Full delivery + advanced AI features (custom scorer weights). |
| `user` | Standard delivery. |

**Enforcement in `ops.py`:**
- `get_active_users(include_admins=False)` filters `is_admin=False` from `tenant_profiles`
- `get_premium_tenants()` returns only `admin` + `premium` rows
- `get_tenant_role(user_id)` returns role string; defaults to `"user"` on any error

**Per-tenant isolation:** Every DB record, every vector RPC call, and every Redis key is namespaced by `user_id`. Two users receiving the same URL get independent processing, independent topic filters, and independent source quality scores.

---

## 4. Stage 1 — Collector

**File:** `src/services/collector/main.py`

### 4.1 Source Loading

```python
sources = get_rss_sources()
# SELECT * FROM rss_sources WHERE is_active = true  (all tenants at once)

cutoff = now(UTC) - timedelta(days=settings.collection_interval_days)
# default: 14 days ago — strict freshness cutoff
```

### 4.2 HTTP Fetch — 3-layer fallback

For each source URL:

```
Attempt 1: HTTP/2 client (httpx, 20s timeout)
  ↓ on ProtocolError / RemoteProtocolError
Attempt 2: HTTP/1.1 client (httpx, 20s timeout)
  ↓ on ConnectError with "CERTIFICATE_VERIFY_FAILED"
Attempt 3: HTTP/1.1 client, verify=False (last resort)
```

**Browser mimicry headers on every request:**
- `User-Agent`: randomly chosen from 4 real Chrome/Firefox UA strings (rotated per source)
- `Accept`, `Accept-Language`, `Accept-Encoding`, `Cache-Control`, `Pragma` set to match a real browser

**Rate limiting:** `time.sleep(random.uniform(3.0, 7.0))` between sources — jitter to avoid bot-detection patterns.

403 responses are logged as warnings and skipped. Empty `feed.entries` triggers a body-snippet debug log.

### 4.3 Per-Article Processing Loop

Processes up to **first 15 entries** per feed. For each entry:

```
1. Extract fields
   link  = entry.link OR entry.id (ArXiv uses 'id')
   title = entry.title[:300]
   content = entry.summary[:2000]

2. Freshness check
   pub_date = published_parsed OR updated_parsed
   if dt < cutoff → SKIP (debug log)

3. URL dedup (Redis)
   normalize_url(link) → MD5 hash → check "seen:{user_id}:{hash}"
   if exists → SKIP

4. Title dedup (Redis)
   alphanumeric slug of title[:100] → check "title:{user_id}:{slug}"
   if exists → SKIP

5. Topic relevance filter (src/services/collector/filter.py)
   Load user's blocked keywords from app_config (cached 5 min per user)
   Search (title + content[:300]).lower() for any blocked keyword
   if match → SKIP (logged as BLOCK, total_skipped++)
   else → PASS (V2 strategy: everything non-blocked goes to the queue)

6. If passed all checks:
   push_to_stream({user_id, title, source_url, source, source_id, content})
     → Redis XADD stream:raw MAXLEN ~ 500 *
   mark_seen(link, user_id)        → Redis SETEX seen:{user_id}:{hash} dedup_ttl
   mark_title_seen(title, user_id) → Redis SETEX title:{user_id}:{slug} dedup_ttl
   update_source_ingestion(source_id, user_id) → Supabase RPC (atomic counter)
   total_queued++
```

### 4.4 Filter Philosophy (V2)

The collector's `is_relevant()` only does **hard blocking** (blocked keyword list). It does **not** positively filter — anything not blocked goes through. The rationale (documented in the code): keyword-only positive matching is too blunt; the semantic Ranker is the right tool for relevance judgment.

### 4.5 Telemetry

```python
log_telemetry("collector", {
    "total_sources": len(sources),
    "queued": total_queued,
    "skipped": total_skipped
})
```

---

## 5. Stage 2 — Enricher

**Files:** `src/services/enricher/embedder.py`, `deduplicator.py`, `novelty.py`, `clusterer.py`

Called inside `process_article_v2()` in `ops.py` for each message read from the Redis stream.

### 5.1 Embedding

```python
embedding = embed_text(article["content"] or title, api_key)
```

- Model: `all-mpnet-base-v2` (Sentence-Transformers, runs **locally**)
- Output: **768-dimensional float list**
- Input truncated to 4000 chars
- Model loaded once using double-checked locking (thread-safe singleton) — concurrent `asyncio.run_in_executor` calls are safe
- `api_key` argument accepted for compatibility but not used

### 5.2 Semantic Deduplication

```python
is_dup = is_near_duplicate(supabase, embedding, user_id)
# RPC: is_near_duplicate(query_embedding, dup_threshold=0.92, p_user_id)
```

- Compares against **all existing articles** for this user in pgvector
- Threshold: cosine similarity ≥ **0.92** → near-duplicate
- If duplicate: Redis message is **acknowledged** (won't be reprocessed), function returns `False`
- This is a **hard gate** — zero further processing

### 5.3 Novelty Scoring

```python
novelty_score = compute_novelty_score(supabase, embedding, user_id, match_count=5)
# RPC: match_articles_recency(query_embedding, match_count=5, p_user_id, decay_rate=0.15)
```

**Algorithm:**
```
1. Fetch top-5 semantically similar articles, each weighted by recency
   (decay_rate=0.15 → older similar articles contribute less)

2. If no similar articles → novelty_score = 1.0 (fully novel)

3. Otherwise:
   avg_similarity = mean(recency_score for each match)
   novelty_score = max(0.0, 1.0 - avg_similarity)
   → rounded to 4 decimal places, range [0.0, 1.0]
```

Score of **1.0** = nothing like this seen before. Score near **0.0** = very similar stories already delivered recently.

### 5.4 Event Clustering

```python
event_id = find_or_create_event(supabase, groq_client, embedding, title, user_id)
# RPC: match_events_by_centroid(query_embedding, threshold=0.85, p_user_id)
```

**Algorithm:**
```
1. Search article_events for a cluster centroid with cosine sim ≥ 0.85

2a. Cluster found:
    Fetch current centroid from article_events
    Update centroid using incremental running average:
      new_centroid[i] = (old[i] × n + new[i]) / (n + 1)
    Increment article_count, set updated_at = now()
    Return existing event_id

2b. No cluster (or RPC unavailable):
    Create new event:
      id = uuid4
      title = first 8 words of article title + "…"
      centroid_embedding = this article's embedding
      article_count = 1
    Return new event_id
```

Dimension mismatch guard: if old centroid has different length than new embedding (e.g. after model change), centroid is reset to the new embedding with a warning logged.

---

## 6. Stage 3 — Ranker

**File:** `src/services/ranker/scorer.py`

### 6.1 Signal Collection

```python
config = get_filter_config(user_id)           # allowed / blocked / priority topics
quality = get_source_quality(source_id, user_id)  # from source_health, default 0.5 (neutral)
```

### 6.2 Topic Match

**If article has no AI-assigned topics yet (raw from Redis stream):**
```python
text = (title + " " + content).lower()
matches = [t for t in allowed_topics if t.lower() in text]
topic_match = 0.8 if matches else 0.4   # heuristic
has_priority = any(t.lower() in priority_topics for t in matches)
```

**If article already has AI-assigned topics:**
```python
# Jaccard ratio: intersection / union
topic_match = |article_topics ∩ allowed_topics| / |article_topics ∪ allowed_topics|
has_priority = any(t in priority_topics for t in article_topics)
```

### 6.3 Scoring Formula

```python
signals = RankSignals(
    base_relevance = float(db_score or 4.0),    # 0–10 from LLM
    novelty_score  = novelty_score,              # 0–1
    source_quality = quality,                    # 0–1
    topic_match    = topic_match,                # 0–1
    priority_boost = 1.0 if has_priority else 0.0
)

score = (base_relevance × 0.35)
      + (novelty_score  × 0.25 × 10)
      + (source_quality × 0.20 × 10)
      + (topic_match    × 0.15 × 10)
      + (priority_boost × 0.05 × 10)

final_score = min(score, 10.0)
```

| Signal | Weight | Max pts |
|---|---|---|
| `base_relevance` (LLM, 0–10) | 35% | 3.5 |
| `novelty_score` (0–1) | 25% | 2.5 |
| `source_quality` (0–1) | 20% | 2.0 |
| `topic_match` (0–1) | 15% | 1.5 |
| `priority_boost` (0 or 1) | 5% | 0.5 |
| **Total** | | **10.0** |

---

## 7. Stage 3.5 — Early Rejection Gate

```python
if final_score < settings.delivery_threshold:  # default: 3.5
    log SKIP with score
    acknowledge_message(GROUP_NAME, msg_id)   # removes from Redis queue
    return False
```

This is the **most important cost-saving gate**. The expensive Research Agent LLM call (Stage 4) only runs for articles that prove their worth here. Articles dropped here are ack'd cleanly — they won't be reprocessed on the next run.

---

## 8. Stage 4 — Research Agent

**File:** `src/services/agents/research_agent.py`  
**Framework:** LangGraph `StateGraph`

### 8.1 Graph Topology

```
EntryPoint → [retrieve_history] → [build_summary] → END
```

State type: `ResearchState` (TypedDict):
```python
{
  article_text, article_title, user_id, embedding,   # inputs
  similar_history,                                    # set by Node 1
  web_context, summary, why_it_matters, topics        # set by Node 2
}
```

### 8.2 Node 1: `retrieve_history`

```python
supabase.rpc("match_articles", {
    "query_embedding": state["embedding"],
    "match_threshold": 0.72,
    "match_count": 3,
    "p_user_id": state["user_id"]
})
```

- Retrieves the **top-3 most semantically related articles** this user has already received
- Threshold 0.72 is deliberately more permissive than dedup (0.92) — wants related stories, not just identical ones
- Returns: title, published_at, summary, why_it_matters per match
- Retried up to 3× with exponential backoff (tenacity)

### 8.3 Node 2: `build_summary`

**Model:** `llama-3.3-70b-versatile` (Groq), temperature=0.1

**Historical context assembled as:**
```
- [2026-04-28] Prior Article Title: prior why_it_matters text (max 120 chars)
- [2026-04-25] Another Article: ...
```

**Prompt:**
```
SYSTEM: You are a precise tech analyst. Summarize articles with historical context.
        {format_instructions}   ← ArticleAnalysis JSON schema

HUMAN: {article_title}\n{article_text[:4000]}
```

**Output** (parsed via `JsonOutputParser` → `ArticleAnalysis` Pydantic model):
```json
{
  "summary": "2-3 sentences of core technical takeaway",
  "why_it_matters": "1 sentence on urgency/impact for a tech professional",
  "topics": ["Category", "tag1", "tag2"],
  "score": 0.0 to 10.0
}
```

**Retry strategy:** `@retry_llm_call(max_attempts=3)` wraps the LLM call. On failure, falls back to:
- `summary` = first 200 chars of article text
- `why_it_matters` = "Error in analysis."
- `topics` = []

---

## 9. Stage 5 — Persist to DB

**Called from:** `ops.py::process_article_v2()` after research agent completes

```python
save_article({
    "user_id":        user_id,
    "source_id":      article["source_id"],
    "title":          title,
    "source_url":     article["source_url"],
    "source":         article["source"],
    "content":        article["content"],
    "embedding":      embedding,           # 768-dim vector
    "novelty_score":  novelty_score,
    "event_id":       event_id,
    "score":          final_score,         # 0–10, from Ranker
    "summary":        result["summary"],   # from Research Agent
    "why_it_matters": result["why_it_matters"],
    "topics":         result["topics"],
    "v2_processed":   True,
})
```

`save_article()` does an **upsert** on conflict `(source_url, user_id)` — so if an article is reprocessed (e.g. pending Redis message), it updates rather than duplicates. Retried up to 3× with exponential backoff.

The Redis message is acknowledged **after** a successful DB save. If the save fails, the message stays in the pending list and will be retried on the next `run all` invocation.

---

## 10. Stage 6 — Composer Agent

**File:** `src/services/agents/composer_agent.py`

Called once per delivery target user, after all articles are saved to DB.

### 10.1 Fetch Top Articles

```python
supabase.table("articles")
  .select("id, title, summary, why_it_matters, source_url, score, novelty_score, created_at")
  .eq("user_id", user_id)
  .eq("is_delivered", False)
  .gte("score", DELIVERY_THRESHOLD)   # 3.5
  .order("score", desc=True)
  .limit(top_n)                       # default: 12
```

### 10.2 Theme Assignment

Each article is assigned to one of 7 fixed themes by keyword matching against `(title + summary).lower()`:

| Theme | Keywords checked |
|---|---|
| Generative AI | llm, gpt, claude, gemini, llama, transformer, fine-tuning |
| Developer Tools | api, sdk, framework, library, release, open source, github |
| Industry | funding, acquisition, startup, ipo, layoffs, valuation |
| Security | vulnerability, breach, cve, exploit, patch, malware |
| Regulation | regulation, policy, gdpr, ban, law, government, compliance |
| Research | paper, arxiv, benchmark, study, dataset, model |
| Quiet Signals | catch-all (no keywords matched) |

First matching theme wins (order above = priority order).

### 10.3 Narrative Intro (LLM)

```python
prompt = f"""Write a 2-sentence tech briefing intro for these stories.
Be direct, no fluff. Start with the most important theme.
Stories:\n{article_titles_joined}"""  # first 8 article titles

intro = groq_client.invoke(prompt).content.strip()
```

Uses the same `llama-3.1-8b-instant` model (lower cost model is fine for a short intro).

### 10.4 Breaking News Detection

```python
breaking = [a for a in articles if a.get("score", 0) >= BREAKING_THRESHOLD]
# BREAKING_THRESHOLD = 8.0
```

### 10.5 Return Value

```python
{
  "empty":    False,
  "intro":    "2-sentence LLM-written briefing intro",
  "breaking": [...],     # articles with score >= 8.0
  "sections": {theme: [articles]},
  "total":    12,
  "user_id":  "..."
}
```

---

## 11. Stage 7 — Delivery Service

**File:** `src/services/delivery/main.py`

### 11.1 Two Execution Paths

**V2 path (normal):** `deliver(digest=digest)` — uses the pre-built digest dict from Composer Agent.

**V1/Legacy path:** `deliver()` with no digest — fetches all undelivered articles from DB directly, groups them by the first topic tag (AI-assigned theme).

### 11.2 Slack Block Kit Payload

Built by `slack_payload(grouped, intro)`:
- Header block: `"Hi {user_name}, here is your TechPulse Digest"`
- Optional intro section (italic)
- Divider
- For each theme: section header → article entries
- Each article entry: `• <url|title>\n  _summary_\n> *Insight:* why_it_matters`
- Accessory button showing the score (clickable, links to article)
- Footer context block: count of delivered articles + Redis queue lag (from `XINFO GROUPS`)

**Slack field truncation** to stay within the 3000-char block limit:
- title: 120 chars
- summary: 250 chars
- why_it_matters: 150 chars

### 11.3 Discord Markdown Payload

Built by `discord_payload_chunks(grouped, intro)`:
- Chunked into multiple messages (each ≤ 1900 chars) to respect Discord's 2000-char limit
- Format: `## Theme\n**1. [title](<url>)** (Score: X.X)\n> summary\n> **Insight:** why_it_matters`
- Stats footer appended to last chunk (or as separate chunk if too long)

### 11.4 Delivery Execution

For each delivery target user:
1. Fetch `tenant_profiles` for webhook URLs
2. POST Slack payload to `slack_webhook_url` (if configured)
3. POST each Discord chunk to `discord_webhook_url` (if configured)
4. `mark_as_delivered(source_urls, user_id)` — sets `is_delivered = True` for all delivered articles
5. `update_source_delivery(source_urls, user_id)` — updates `source_health` quality scores

### 11.5 Source Quality Update on Delivery

`update_source_delivery()` in `shared/db.py`:
```
For each delivered article:
  1. Resolve source_id from articles table
  2. Fetch existing source_health row
  3. Increment articles_delivered
  4. Recompute quality_score = articles_clicked / articles_delivered
     (stays 0.5 neutral until first click is recorded)
  5. Upsert source_health row
```

This means source quality is a **lagging signal** — it reflects historical engagement, not current article quality.

---

## 12. Parallel Path — Legacy Summarizer

**File:** `src/services/summarizer/main.py`  
**Command:** `uv run techpulse-ops run summarize`

This is the V1/standalone pipeline — it does summarization **without** the full V2 enricher/ranker/research-agent chain. It's useful for batch-processing queued articles independently.

### Flow

```
Read up to 60 messages from Redis stream (summarizer-group)
  ↓ for each message (concurrent, semaphore=max_concurrency):
    1. Load user filter config (allowed/blocked/priority)
    2. Blocked keyword check → if blocked: ack message, skip
    3. Call Groq LLM (llama-3.1-8b-instant, temp=0.3):
       Prompt includes: allowed_topics, title, source, content[:1500]
       Returns: score (0–10), summary, why_it_matters, topics[]
    4. Priority boost: if article topics intersect user priority list → +1.5 to score
    5. Early DB rejection: if score < 3.0 → ack, skip (no DB write)
    6. Save to Supabase articles table (with v2_processed=True)
    7. Acknowledge Redis message
    8. Rate limit sleep: 3 seconds (Groq ~20 RPM compliance)
```

### LLM Scoring Criteria (Summarizer prompt)

The LLM is instructed to score on this scale:
- Relevance to the user's Target Topics: **0–5.0 pts**
- Technical depth and insight: **0–3.0 pts**
- Novelty and importance: **0–2.0 pts**
- Total: **0–10.0**

The first topic in the `topics` list MUST be a concise category (e.g., "Python", "Cloud", "AI Research") — this is the theme used by the delivery service for grouping.

---

## 13. Feedback Loop

**File:** `src/services/ranker/feedback_processor.py`  
**Command:** `uv run techpulse-ops run feedback-loop --days 7`

### Signal Types

| Signal | Counted as |
|---|---|
| `clicked` | Positive |
| `saved` | Positive |
| `more_like_this` | Positive |
| `dismissed` | Negative |
| `less_like_this` | Negative |

### Algorithm

```
1. Fetch all user_feedback rows created in the last N days
   (joined with articles to get source name)

2. Build source_id map: (user_id, source_name) → source_id
   (workaround: articles table may not always have source_id)

3. Aggregate per (user_id, source_id):
   positive_count = sum of positive signals
   negative_count = sum of negative signals

4. For each (user_id, source_id):
   If no existing source_health row:
     delivered = positive + negative
     clicked = positive
     quality = min((clicked + 1) / (delivered + 2), 1.0)  ← Laplace smoothing
     INSERT new row

   If row exists:
     new_clicked = existing_clicked + positive_count
     new_delivered = existing_delivered + negative_count
     new_quality = min(new_clicked / max(new_delivered, 1), 1.0)
     UPDATE row
```

The resulting `quality_score` feeds directly into Stage 3 Ranker's `source_quality` signal. Sources with high click rates get promoted; sources with many dismissals get demoted.

---

## 14. Redis Stream Mechanics

**File:** `src/shared/redis_client.py`  
**Stream key:** `stream:raw`  
**Consumer group:** `summarizer-group`  
**Consumer name:** `worker-1`

### Producer (Collector)

```python
XADD stream:raw MAXLEN ~ 500 * field1 val1 field2 val2 ...
```

`MAXLEN ~ 500` caps the stream at approximately 500 messages (approximate trim for performance). Fields stored: `user_id`, `title`, `source_url`, `source`, `source_id`, `content`.

### Consumer Group Pattern

`read_from_group()` uses a **two-phase read** for reliability:

```
Phase 1: XREADGROUP GROUP summarizer-group worker-1 COUNT 50 STREAMS stream:raw 0
  → reads messages previously delivered to this consumer but not yet ACKed (pending)
  → "0" means start from the beginning of pending list for this consumer

Phase 2 (only if Phase 1 returns nothing):
  XREADGROUP GROUP summarizer-group worker-1 COUNT 50 STREAMS stream:raw >
  → reads NEW messages not yet delivered to any consumer in the group
  → ">" means "give me only new messages"
```

This guarantees **at-least-once delivery**: if a worker crashes mid-processing, the message stays in pending and is retried on the next run.

### Acknowledgement

```python
XACK stream:raw summarizer-group {msg_id}
```

Called **only after** successful processing (DB save confirmed). Until this call, the message stays in the pending list.

### URL/Title Dedup Keys

```
seen:{user_id}:{md5_of_normalized_url}   TTL: dedup_ttl_days × 86400  (default 7 days)
title:{user_id}:{alphanumeric_slug[:100]} TTL: same
```

`normalize_url()` in `shared/utils.py` strips tracking params and normalizes the URL before hashing.

---

## 15. Key Data Structures

### `ArticleAnalysis` (Pydantic model — `shared/models.py`)

The canonical output schema shared by both the Summarizer and Research Agent:

```python
class ArticleAnalysis(BaseModel):
    summary:        str            # 2-3 sentence technical takeaway
    why_it_matters: str            # 1 sentence on urgency/impact
    topics:         List[str]      # max 3 tags; first one is the primary category
    category:       Optional[str]  # primary theme (sometimes set separately)
    score:          Optional[float]  # 0.0–10.0
```

### `RankSignals` (dataclass — `ranker/scorer.py`)

```python
@dataclass
class RankSignals:
    base_relevance: float   # 0–10: LLM score from summarizer
    novelty_score:  float   # 0–1: from enricher/novelty
    source_quality: float   # 0–1: from source_health table
    topic_match:    float   # 0–1: Jaccard or heuristic
    priority_boost: float   # 1.0 or 0.0: priority topic match
```

### `ResearchState` (TypedDict — `agents/research_agent.py`)

LangGraph state that flows through the research graph:

```python
{
    "article_text":    str,
    "article_title":   str,
    "user_id":         str,
    "embedding":       List[float],    # 768-dim
    "similar_history": List[Dict],     # from Node 1: top-3 related articles
    "web_context":     str,            # unused in current implementation
    "summary":         str,            # set by Node 2
    "why_it_matters":  str,            # set by Node 2
    "topics":          List[str],      # set by Node 2
}
```

### Digest Dict (from `composer_agent.py`)

```python
{
    "empty":    bool,
    "intro":    str,            # LLM-written 2-sentence narrative intro
    "breaking": List[Dict],     # articles with score >= 8.0
    "sections": {
        "Generative AI": [...],
        "Developer Tools": [...],
        ...
    },
    "total":    int,
    "user_id":  str,
}
```

---

## 16. Configuration & Thresholds

**File:** `src/shared/config.py` — loaded from `.env` via Pydantic-Settings

| Setting | Default | Effect |
|---|---|---|
| `groq_model` | `llama-3.1-8b-instant` | Model used by summarizer (fast, cheap) |
| `top_n_articles` | `12` | Max articles fetched per delivery run |
| `dedup_ttl_days` | `7` | How long URL/title dedup keys live in Redis |
| `collection_interval_days` | `14` | Freshness cutoff for RSS articles |
| `near_duplicate_threshold` | `0.92` | Cosine similarity above which articles are dropped as duplicates |
| `delivery_threshold` | `3.5` | Minimum score for digest inclusion (Stage 3.5 gate) |
| `breaking_threshold` | `8.0` | Score above which articles are flagged as breaking news |
| `max_concurrency` | `3` | Max parallel LLM calls in pipeline |

### Score Interpretation Guide

| Score range | Meaning | Action |
|---|---|---|
| 0.0 – 2.9 | Irrelevant / noise | Dropped by summarizer (score < 3.0), never saved to DB |
| 3.0 – 3.4 | Low quality | Saved to DB but dropped at Stage 3.5 rejection gate |
| 3.5 – 5.9 | Acceptable | Included in regular digest |
| 6.0 – 7.9 | High quality | Top of digest, prominent placement |
| 8.0 – 10.0 | Breaking / critical | Flagged as breaking news, shown first in digest |

---

## End-to-End Article Lifecycle Example

```
Article URL:  https://arxiv.org/abs/2405.12345
              "Introducing Llama-4: Meta's Next Frontier Model"

[Collector]
  → HTTP/2 fetch arxiv RSS feed
  → pub_date = today → passes freshness check
  → check Redis: "seen:{user_id}:{md5}" → not found → passes URL dedup
  → check Redis: "title:{user_id}:{slug}" → not found → passes title dedup
  → user has no "arxiv" in blocked list → passes topic filter
  → XADD stream:raw → message ID "1746270000000-0"
  → mark_seen, mark_title_seen (Redis TTL 7 days)
  → RPC increment_source_ingestion

[Enricher — via ops.py process_article_v2]
  → embed_text(content) → 768-dim vector
  → is_near_duplicate(embedding) → False (no similar article in DB)
  → novelty_score = 0.91 (only 1 slightly similar article in history)
  → event_id = find_or_create_event → creates new cluster "Introducing Llama-4…"

[Ranker]
  → source_quality = 0.72 (arxiv has high historical quality for this user)
  → topic_match = 0.8 (heuristic: "llama" found in text, in user's allowed topics)
  → has_priority = True (user has "LLM" in priority list)
  → signals: base_relevance=4.0 (fallback), novelty=0.91, quality=0.72, match=0.8, priority=1.0
  → score = (4.0×0.35) + (0.91×2.5) + (0.72×2.0) + (0.8×1.5) + (1.0×0.5)
           = 1.4 + 2.275 + 1.44 + 1.2 + 0.5 = 6.815
  → 6.815 >= 3.5 → passes rejection gate

[Research Agent]
  → Node 1: match_articles(embedding, threshold=0.72) → finds 2 prior Llama-3 articles
  → Node 2: build_summary with historical context
     LLM sees: "- [2026-04] Llama 3: Meta's open model strategy..."
     Generates:
       summary: "Meta released Llama-4 as a 400B MoE model with 10M token context window..."
       why_it_matters: "This directly challenges GPT-4o on cost-performance ratio for enterprise deployments."
       topics: ["AI Research", "llm", "meta"]

[Persist]
  → save_article({..., score=6.815, summary=..., why_it_matters=..., topics=[...], v2_processed=True})
  → upsert on conflict (source_url, user_id)
  → XACK stream:raw summarizer-group 1746270000000-0

[Composer]
  → Fetches this article (score 6.815 > 3.5, is_delivered=False)
  → assign_theme: "llm" matches → theme = "Generative AI"
  → Builds digest sections, LLM writes intro

[Delivery]
  → slack_payload: block with title, italic summary, "Insight:" why_it_matters, score button
  → POST to user's slack_webhook_url
  → mark_as_delivered([source_url], user_id) → is_delivered = True
  → update_source_delivery → increments arxiv source_health.articles_delivered
```
