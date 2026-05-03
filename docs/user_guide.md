# TechPulse AI User Guide

Welcome to your personalized tech intelligence dashboard. This guide explains how to configure your sources and topics to get the most out of the Agentic AI pipeline.

## 📋 User Tiers

### Standard User
Standard users receive high-quality tech digests based on curated global feeds and their own basic filters.
- **Topics**: Up to 5 allowed and 5 blocked keywords.
- **Sources**: Up to **5** personal RSS feeds (quota-enforced).

### Premium User
Premium users have full control over the intelligence pipeline, including priority boosting and bulk source management.
- **Topics**: Unlimited keywords + **Priority Boosting** (force high scores for critical interests).
- **Sources**: Up to **50** personal RSS feeds + **Bulk Import** support.

---

## 🏗️ How to Add RSS Feeds

You can manage your news sources using the `techpulse sources` command group.

### 1. Add a Single Source (Standard/Premium)
Use the `add` command to register a new feed by its URL:
```bash
uv run techpulse sources add "Python News" "https://www.python.org/blogs/feed/"
```

### 2. Bulk Import (Premium Only)
If you have a list of feeds in a text file (one per line, format: `Name | URL`), you can import them all at once:
```bash
uv run techpulse sources import my_feeds.txt
```

### 3. List or Remove Sources
```bash
# See what you are currently tracking
uv run techpulse sources list

# Remove a source you no longer need
uv run techpulse sources remove "https://old-blog.com/feed"
```

---

## 🎯 How to Manage Topic Filters

Topic filters control what the AI ranks as "relevant" for you.

### 1. Set Your Interests
Use the `topics set` command to update your filters. Use commas to separate multiple keywords.

```bash
# Standard Filter
uv run techpulse topics set --allowed "ai, robotics, space" --blocked "crypto, gaming"
```

### 2. Set Priority Boosts (Premium Only)
Priority keywords give a significant score boost (+1.5 pts) to any article that mentions them, ensuring they appear at the top of your digest.

```bash
# Premium Filter with Priority
uv run techpulse topics set --allowed "rust, devops" --priority "security, k8s"
```

### 3. View Current Config
```bash
uv run techpulse topics show
```

---

## 🚀 Getting Started Flow
1. **Login**: `uv run techpulse login`
2. **Setup Topics**: `uv run techpulse topics set --allowed "python, ai"`
3. **Add Feeds**: `uv run techpulse sources add "TechCrunch" "https://techcrunch.com/feed"`
4. **Check Status**: `uv run techpulse status` (See how many articles are pending for you)
