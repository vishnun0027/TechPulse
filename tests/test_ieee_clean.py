import asyncio
from shared.db import supabase
from cli.pipeline import process_article_v2
from services.agents.research_agent import build_research_agent
from shared.config import settings
from loguru import logger
from unittest.mock import patch

async def test_ieee_image_cleaning():
    logger.info("Testing Research Agent with IEEE Spectrum image tag")

    html_content = """
    <img src="https://spectrum.ieee.org/media-library/illustration.jpg" width="1245">
    <p>With $1 Cyberattacks on the Rise, Durable Defenses Pay Off. New research shows that automated script-kiddie attacks are getting cheaper, but robust architectural defenses are still effective. This study highlights the importance of multi-layered security in the face of increasingly automated and low-cost threat actors.</p>
    """

    msg = {
        "id": "test-ieee-html",
        "data": {
            "user_id": "00000000-0000-0000-0000-000000000000",
            "title": "Durable Defenses Pay Off",
            "content": html_content,
            "source": "IEEE Spectrum",
            "source_url": "https://example.com/ieee-defenses",
            "score": 4.5
        }
    }

    agent = build_research_agent(supabase, settings.groq_api_key)
    semaphore = asyncio.Semaphore(1)

    logger.info("Running Research Agent...")
    with patch("cli.pipeline.acknowledge_message"), patch("shared.db.save_article", return_value=True):
        success = await process_article_v2(supabase, msg, agent, settings.groq_api_key, semaphore)

    if success:
        logger.info("✅ Success! Image tag was removed and article processed.")
    else:
        logger.error("❌ Failed to process IEEE content.")

if __name__ == "__main__":
    asyncio.run(test_ieee_image_cleaning())
