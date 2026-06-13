# TechPulse — User Configuration Reference

> What a user must configure for the pipeline to work correctly.

---

## Overview

TechPulse is multi-tenant. Every user has their own isolated configuration stored in Supabase. The pipeline reads these at runtime — nothing is hardcoded per user. If any of these are missing, the pipeline still runs but with degraded or neutral behaviour (documented below).

---

## 1. Tenant Profile (`tenant_profiles` table)

Created automatically on first signup/login via Supabase Auth. Must have:

| Field | Required | What it does |
|---|---|---|
| `user_id` | ✅ | UUID from Supabase Auth — primary key for all per-user data |
| `email` | ✅ | Used for display in CLI and logging |
| `role` | ✅ | One of `admin`, `auditor`, `premium`, `user` (retained in DB schema for future web UI gating, but treated equally by the V2 backend pipeline) |
| `slack_webhook_url` | ⚠️ At least one | Where digests are delivered |
| `discord_webhook_url` | ⚠️ At least one | Where digests are delivered |
| `full_name` | Optional | Used as greeting in digest header (`"Hi {name}, here is your digest"`) |

> **If neither webhook is set:** the pipeline runs collect → score ➔ save, but delivery is silently skipped for that user.

---

## 2. Topic Configuration (`app_config` table)

**Key:** `"topics"` | **Value:** JSON object representing user interests:

```json
{
  "allowed":  ["Gen AI", "agentic AI", "LLM", "Python", "Cloud"],
  "blocked":  ["crypto", "NFT", "sports"],
  "priority": ["agentic AI", "LLM"]
}
```

| Field | Required | Effect when missing |
|---|---|---|
| `allowed` | ⚠️ Strongly recommended | LLM relevance scores against "General high-quality tech intelligence" — low signal, compressed scores |
| `blocked` | Optional | Nothing is ever hard-blocked at the collector |
| `priority` | Optional | No priority_boost signal is applied in ranking formula |

### How each field is used in the pipeline

*   **`allowed`:**
    *   Collector: ignored (not a hard gate)
    *   Summarizer LLM prompt: passed as "Target Topics" — directly influences the LLM's relevance score (0–5 of the 0–10 scale)
    *   Ranker: used to compute `topic_match` via Jaccard ratio (15% of final score)
*   **`blocked`:**
    *   Collector: any article where `(title + content[:300]).lower()` contains a blocked keyword is dropped before queuing — **hard gate**
*   **`priority`:**
    *   Ranker: `priority_boost = 1.0` if match, else `0.0` (contributes 0.5 pts max to final score)

---

## 3. RSS Sources (`rss_sources` table)

Each row represents one feed for one user.

| Field | Required | Notes |
|---|---|---|
| `user_id` | ✅ | Links to tenant |
| `url` | ✅ | Full RSS/Atom feed URL |
| `name` | ✅ | Display name (used in logs, source_health tracking, digest footer) |
| `is_active` | ✅ | Set to `true` — inactive sources are skipped entirely by collector |

**Managed via CLI:**
```bash
uv run pulse feeds list
uv run pulse feeds add <url> --name "My Feed"
uv run pulse feeds remove <id>
```

> **If no sources exist:** the collector has nothing to fetch — the entire pipeline produces zero articles for that user.

---

## 4. Delivery Webhooks

Set in `tenant_profiles.slack_webhook_url` and/or `discord_webhook_url`.

*   **Slack:** Incoming Webhook URL from a Slack App (`https://hooks.slack.com/services/...`)
*   **Discord:** Channel webhook URL (`https://discord.com/api/webhooks/...`)

At least one must be set for the user to receive digests. These are typically set via Supabase Dashboard, custom scripts, or directly in the DB schema.

---

## 5. What happens with missing config (degraded mode)

| Missing | Behaviour |
|---|---|
| No `app_config` / `allowed` is empty | LLM uses generic scoring. `topic_match = 0.5` (neutral). Scores compress to 4–5 range. |
| No `blocked` | Nothing blocked at collector — all non-stale, non-duplicate articles queued |
| No `priority` | No score boosts. Breaking threshold (8.0) unlikely to be reached. |
| No RSS sources | Zero articles collected for this user |
| No webhooks | Articles collected, scored, saved — but digest never sent |

---

## 6. Minimum viable configuration checklist

For a user to get meaningful digests:

*   [ ] `tenant_profiles` row exists with valid `user_id`
*   [ ] At least one webhook configured (`slack_webhook_url` or `discord_webhook_url`)
*   [ ] `app_config` row with key `"topics"` and a meaningful `allowed` list (5–10 topics)
*   [ ] At least a few RSS sources with `is_active = true`
