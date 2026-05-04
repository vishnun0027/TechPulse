import asyncio
from shared.db import supabase
from cli.ops import process_article_v2
from services.agents.research_agent import build_research_agent
from shared.config import settings
from loguru import logger
from unittest.mock import patch

async def test_html_cleaning():
    logger.info("Testing Research Agent with raw HTML content (cPanel mockup)")
    
    html_content = """
    <h4>Exploitation was underway before patches landed, at least one victim reports ransomware demand</h4>
    <p>CISA has added a critical cPanel bug to its known-exploited list, confirming that attackers are actively using the vulnerability.</p>
    <p>The flaw, tracked as CVE-2024-XXXX, allows unauthenticated attackers to execute arbitrary code on the host system.</p>
    <div><p>Millions of sites are potentially exposed due to the popularity of the hosting control panel.</p></div>
    """
    
    msg = {
        "id": "test-cpanel-html",
        "data": {
            "user_id": "00000000-0000-0000-0000-000000000000",
            "title": "Critical cPanel Vulnerability",
            "content": html_content,
            "source": "The Register",
            "source_url": "https://example.com/cpanel-bug",
            "score": 4.5
        }
    }
    
    agent = build_research_agent(supabase, settings.groq_api_key)
    semaphore = asyncio.Semaphore(1)
    
    logger.info("Running Research Agent on Sanitized HTML...")
    with patch("cli.ops.acknowledge_message"), patch("shared.db.save_article", return_value=True):
        success = await process_article_v2(supabase, msg, agent, settings.groq_api_key, semaphore)
    
    if success:
        logger.info("✅ Success! HTML was sanitized and processed.")
    else:
        logger.error("❌ Failed to process HTML content.")

if __name__ == "__main__":
    asyncio.run(test_html_cleaning())
