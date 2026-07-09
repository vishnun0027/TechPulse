# TechPulse Documentation 📚

Welcome to the technical documentation for TechPulse. Choose a guide below to get started:

### 👤 For Users
*   **[User Guide](user_guide.md)**: How to use the `pulse` CLI to manage your news feeds and topics.
*   **[User Configuration](user_config.md)**: Details on the `app_config` and `rss_sources` tables for custom tenant setup.

### 🛠️ For Developers & Operators
*   **[Architecture Deep Dive](dataflow.md)**: A comprehensive guide to the 7-stage agentic pipeline, scoring formulas, and Redis mechanics.
*   **[Developer Guide](developer_guide.md)**: Technical overview of the multi-tenant security model, RLS, and development environment.
*   **[Coding Standards](coding_standards.md)**: Strict standards for formatting, linting, and professional output.

---

### 🚀 Quick Links
*   [Main README](../README.md)
*   [GitHub Workflows](../.github/workflows/)

---

## 🗺️ Roadmap & Future Plans

The following features and architectures are planned to enhance the TechPulse intelligence platform:

### 1. Daily Audio Podcasting (AI Voice Briefings)
- **Concept**: Automatically generate a daily 3-to-5 minute audio briefing summarizing the morning's top stories.
- **Details**: Integrate ElevenLabs or OpenAI TTS to synthesize the daily summary text into a high-quality `.mp3` podcast file, delivered directly inside Slack/Discord digests.

### 2. Self-Learning Ranker (Dynamic Weight Adaptation)
- **Concept**: Shift scoring weights dynamically based on active user feedback signals instead of using static configurations.
- **Details**: Run a daily regression analysis on user click/rating histories to adjust individual weights (`base_relevance`, `novelty_score`, etc.) dynamically per tenant.

### 3. Premium Web Dashboard (Next.js & Tailwind CSS)
- **Concept**: A unified dark-mode SaaS portal at `nullnex.com` to manage ingestion, configurations, and read history.
- **Details**: Provide visual tools for managing feed sources, editing filters, executing cited semantic searches, and monitoring developer/AI metrics.

### 4. Multi-Agent Contrarian Analysis (Bias Reduction)
- **Concept**: Leverage multiple specialized LangGraph agents to gather opposing perspectives on major tech updates.
- **Details**: One agent summarizes the main story, a second agent searches for critiques/concerns, and a third synthesizes a balanced, objective report.

### 5. Multi-Modal Ingestion (YouTube & Twitter/X)
- **Concept**: Expand collection channels to include video transcripts and developer social media threads.
- **Details**: Build collectors to ingest and clean YouTube transcripts and target Twitter/X list threads, feeding them directly into the V2 scoring pipeline.
