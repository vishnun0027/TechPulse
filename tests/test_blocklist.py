import asyncio
from shared.db import supabase
from cli.ops import process_article_v2
from services.agents.research_agent import build_research_agent
from shared.config import settings
from loguru import logger
from unittest.mock import patch

async def test_blocklist():
    logger.info("Testing Noise Reduction (Ferrari/Automotive blocklist)")
    
    msg = {
        "id": "test-noise-ferrari",
        "data": {
            "user_id": "ab05c507-fd62-44c4-80de-c1b2ae4f0bf2",
            "title": "New Ferrari Purosangue Review",
            "content": "The Ferrari Purosangue is a high-performance luxury SUV that pushes the boundaries of automotive engineering with its naturally aspirated V12 engine and advanced suspension system. This vehicle represents the pinnacle of Italian automotive design and performance, offering a driving experience that is unmatched in the luxury SUV segment.",
            "source": "TopGear",
            "source_url": "https://example.com/ferrari-purosangue",
            "score": 8.0,
            "topics": ["Ferrari", "Automotive", "Luxury Cars"]
        }
    }
    
    agent = build_research_agent(supabase, settings.groq_api_key)
    semaphore = asyncio.Semaphore(1)
    
    logger.info("Running Ranker on Blocked Topic...")
    with patch("cli.ops.acknowledge_message"), patch("shared.db.save_article") as mock_save:
        success = await process_article_v2(supabase, msg, agent, settings.groq_api_key, semaphore)
        
        if not success:
            logger.info("✅ Success! Article was REJECTED by the blocklist.")
        else:
            # Check the score passed to save
            save_args = mock_save.call_args[0][0]
            logger.info(f"Final Score: {save_args['score']}")
            if save_args['score'] == 0.0:
                logger.info("✅ Success! Score was set to 0.0 by blocklist.")
            else:
                logger.error("❌ Blocklist failed to zero-out the score.")

if __name__ == "__main__":
    asyncio.run(test_blocklist())
