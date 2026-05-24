# TechPulse AI 🤖

[![CI/CD Pipeline](https://github.com/vishnun0027/techpulse-ai/actions/workflows/ci-cd.yml/badge.svg)](https://github.com/vishnun0027/techpulse-ai/actions/workflows/ci-cd.yml)

### *Your personal tech intelligence system, curated by Agentic AI.*

TechPulse AI is an intelligent curation assistant designed to monitor tech feeds, eliminate duplicate coverage, track topic novelty, and deliver tailored, high-value narrative digests straight to your Slack or Discord channels.

---

## 🌟 The Core Idea & Vision
Modern software engineers, developers, and tech leaders are constantly inundated with technical news, security alerts, and framework releases. Keeping up is critical, but the sheer volume of duplicate headlines and low-quality noise makes it incredibly time-consuming.

**TechPulse AI** is built on a simple vision: to turn passive, noisy news feeds into an active tool for professional growth. The system answers a single, essential question for every article it processes: 

> *"Tell me only what changed, why it matters, and what I should watch next."*

By analyzing the semantic meaning of stories rather than relying on basic keyword filters, TechPulse AI filters out redundancy, measures the novelty of incoming coverage, and explains the real-world impact of technology developments before delivering a consolidated morning digest.

---

## ✨ Key Features

### 1. Ingestion & Filtering
Easily subscribe to technical blogs, developer channels, or global news feeds. The system automatically fetches and queues incoming articles, filtering out noise and keeping your queue fresh.

### 2. Intelligent Deduplication & Novelty Tracking
Suppresses "same story, different headline" repeats by evaluating the underlying meaning of articles. The engine measures how novel an incoming article is compared to what you have already read, showing you only genuinely fresh insights.

### 3. Personalized Relevance Ranking
Tailor your digest through allowed, blocked, and prioritized interests. The engine scores each article dynamically, ensuring critical topics are boosted to the top of your brief while irrelevant categories are muted.

### 4. Context-Aware "Why It Matters" Takeaways
Instead of raw links, TechPulse reads the context of current and past related articles to generate a concise summary and a dedicated analysis of the story's real-world impact.

### 5. Theme-Grouped Narrative Briefs
Articles are organized into clear technical themes (such as *Generative AI*, *Developer Tools*, *Security*, and *Research*), accompanied by a narrative summary explaining the day's main technical updates.

### 6. Seamless Workplace Delivery
Get your curated intelligence delivered directly to Slack or Discord as clean, formatted briefs designed for rapid reading.

---

## 🔄 How It Works

```
[ Tech Feeds ] ──> [ Ingestion & Dedup ] ──> [ Personal Interest Ranking ] ──> [ Impact Analysis ] ──> [ Tailored Digest ]
```

*   **Ingest**: Pulls articles from your configured feeds.
*   **Filter & Dedup**: Ignores exact duplicates and stories you've already seen.
*   **Rank**: Applies your custom filters to score and prioritize articles.
*   **Analyze**: Synthesizes the core news and highlights its specific importance.
*   **Deliver**: Packages the highest-scoring updates into theme-based Slack or Discord briefs.

---

## 🎯 Who Is It For?
*   🧑‍💻 **Developers & Architects**: Track new library releases, tooling updates, and framework patterns.
*   🛡️ **Security Professionals**: Stay on top of CVE vulnerabilities, exploits, and regulatory compliance.
*   🧠 **AI & ML Engineers**: Monitor new research papers, model releases, and benchmarks.
*   📈 **Tech Leaders & Managers**: Keep a pulse on industry shifts, funding rounds, and product launches.

---

### About
A CLI-driven, agentic tech intelligence system featuring automated feed collection, semantic deduplication, personalized interest filtering, and Slack/Discord digest delivery.

### Topics
`python` `cli` `typer` `rich` `rss-feed` `redis` `supabase` `groq` `langchain` `langgraph` `rag` `pgvector` `slack-bot` `discord-bot` `uv` `developer-tools` `automated-news`

### Resources
*   📖 [Documentation Center](docs/README.md)
*   🐍 [Python 3.12](https://www.python.org/)
*   ⚡ [uv Package Manager](https://github.com/astral-sh/uv)

### Languages
*   🐍 **Python**: 85.8%
*   🗄️ **PLpgSQL**: 11.4%
*   🐚 **Shell**: 1.8%
*   🛠️ **Makefile**: 1.0%
