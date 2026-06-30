from pydantic import BaseModel, Field
from typing import List, Optional


class ArticleAnalysis(BaseModel):
    """
    Standardized AI analysis structure for tech articles.
    Used across Summarizer, Research Agent, and Delivery layers.
    """

    summary: str = Field(
        description="A concise, high-signal summary of the article's core technical takeaway."
    )
    why_it_matters: str = Field(
        description="Briefly explains the specific urgency or impact for a tech professional."
    )
    topics: List[str] = Field(
        description="List of specific technical tags extracted from the content (max 3)."
    )
    score: Optional[float] = Field(
        None, ge=0.0, le=10.0, description="Relevance score (0-5 in V1, 0-10 in V2)."
    )
