import asyncio
from shared.db import supabase
from cli.pipeline import process_article_v2
from services.agents.research_agent import build_research_agent
from shared.config import settings
from loguru import logger

async def test_qwen_research():
    logger.info(f"Testing Research Agent with model: {settings.groq_research_model}")

    # 1. Create a dummy message
    msg = {
        "id": "test-msg-qwen",
        "data": {
            "user_id": "00000000-0000-0000-0000-000000000000",
            "title": "Quantum Photonics Breakthrough",
            "content": "Researchers have developed a new topological insulator that allows light to travel around corners without scattering, enabling massive scaling in optical computing architectures. This breakthrough effectively solves the signal degradation issues that have plagued silicon photonics for the last decade, paving the way for 100Tbps chip-to-chip interconnects in next-generation AI clusters.",
            "source": "TechPulse-Test",
            "source_url": f"https://example.com/q-photonics-{id(123)}",
            "score": 4.5
        }
    }

    agent = build_research_agent(supabase, settings.groq_api_key)
    semaphore = asyncio.Semaphore(1)

    # 2. Run processing (this will trigger Stage 4: Research)
    logger.info("Running Research Agent...")
    from unittest.mock import patch
    with patch("cli.pipeline.acknowledge_message"):
        success = await process_article_v2(supabase, msg, agent, settings.groq_api_key, semaphore)

    if success:
        logger.info("✅ Success! Research Agent completed.")
        # Fetch the result
        res = supabase.table("articles").select("*").eq("title", "Quantum Photonics Breakthrough").order("created_at", desc=True).limit(1).execute()
        if res.data:
            article = res.data[0]
            print("\n--- TEST RESULT ---")
            print(f"Title: {article['title']}")
            print(f"Summary: {article['summary']}")
            print(f"Why It Matters: {article['why_it_matters']}")
            print(f"Topics: {article['topics']}")
            print("-------------------\n")
    else:
        logger.error("❌ Research Agent failed.")

if __name__ == "__main__":
    asyncio.run(test_qwen_research())
