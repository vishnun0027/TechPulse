from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
import urllib.parse
from typing import List, Optional
import hmac
import hashlib
from shared.db import supabase
from shared.config import settings
from api.deps import get_current_user_id
from pydantic import BaseModel
from loguru import logger

router = APIRouter()

class ArticleResponse(BaseModel):
    id: str
    title: str
    summary: Optional[str]
    why_it_matters: Optional[str]
    source_url: str
    source: str
    score: float
    topics: List[str]
    is_delivered: bool
    created_at: str

class FeedbackRequest(BaseModel):
    signal: str # clicked, saved, dismissed, more_like_this, less_like_this

@router.get("/", response_model=List[ArticleResponse])
def get_articles(
    user_id: str = Depends(get_current_user_id),
    min_score: float = Query(3.5, ge=0.0, le=10.0),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    is_delivered: Optional[bool] = None,
):
    """Fetches curated articles for the current user."""
    try:
        query = (
            supabase.table("articles")
            .select("*")
            .eq("user_id", user_id)
            .gte("score", min_score)
            .order("score", desc=True)
            .range(offset, offset + limit - 1)
        )

        if is_delivered is not None:
            query = query.eq("is_delivered", is_delivered)

        res = query.execute()
        return res.data or []
    except Exception as e:
        logger.error(f"Error fetching articles: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch articles.")


@router.post("/{article_id}/feedback")
def submit_feedback(
    article_id: str,
    request: FeedbackRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Submits user feedback for an article to power the feedback loop."""
    try:
        # Verify article exists and belongs to user
        check = supabase.table("articles").select("id").eq("id", article_id).eq("user_id", user_id).execute()
        if not check.data:
            raise HTTPException(status_code=404, detail="Article not found or access denied.")

        supabase.table("user_feedback").insert({
            "article_id": article_id,
            "user_id": user_id,
            "signal": request.signal
        }).execute()
        return {"status": "success", "message": f"Feedback recorded."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to record feedback for article {article_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to record feedback.")


@router.get("/{article_id}/click")
def redirect_article(
    article_id: str,
    user_id: str,
    redirect: str,
    token: str,
):
    """
    Logs a 'clicked' feedback event for an article, then redirects to the original URL.
    Validates the click action using a signed HMAC token to prevent tampering and open redirects.
    """
    secret = settings.jwt_secret or settings.encryption_key
    if not secret:
        logger.error("Signing secret is not configured")
        raise HTTPException(status_code=500, detail="Internal server error.")

    # Validate HMAC signature to prevent parameter tampering
    expected_msg = f"click:{article_id}:{user_id}:{redirect}".encode("utf-8")
    expected_token = hmac.new(secret.encode("utf-8"), expected_msg, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(token, expected_token):
        logger.warning(f"Invalid click token signature for article {article_id}")
        raise HTTPException(status_code=403, detail="Forbidden: Invalid click token.")

    # Parse and validate target URL to prevent SSRF and Open Redirect to internal networks
    target_url = urllib.parse.unquote(redirect)
    parsed_url = urllib.parse.urlparse(target_url)

    if parsed_url.scheme not in ("http", "https"):
        logger.warning(f"Rejected click redirect to invalid scheme: {parsed_url.scheme}")
        raise HTTPException(status_code=400, detail="Invalid redirect URL scheme.")

    BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "169.254.169.254"}
    hostname = parsed_url.hostname
    if hostname:
        hostname_lower = hostname.lower()
        if hostname_lower in BLOCKED_HOSTS or hostname_lower.endswith(".internal"):
            logger.warning(f"Rejected click redirect to blocked/internal host: {hostname}")
            raise HTTPException(status_code=400, detail="Redirect to internal hosts is not allowed.")

    try:
        # Log feedback
        supabase.table("user_feedback").insert({
            "article_id": article_id,
            "user_id": user_id,
            "signal": "clicked"
        }).execute()

        # Increment clicks count in telemetry/source health
        article_res = supabase.table("articles").select("source_id").eq("id", article_id).single().execute()
        if article_res.data and article_res.data.get("source_id"):
            supabase.rpc(
                "increment_source_click",
                {"p_source_id": article_res.data["source_id"], "p_user_id": user_id}
            ).execute()
    except Exception as e:
        logger.error(f"Error logging click telemetry: {e}")

    return RedirectResponse(url=target_url)


@router.get("/{article_id}/action", response_class=HTMLResponse)
def submit_chat_action(
    article_id: str,
    user_id: str,
    signal: str,
    token: str,
):
    """
    Records direct article feedback (saved, dismissed, more_like_this, less_like_this)
    via public action links, returning a clean feedback confirmation page.
    Requires a valid HMAC token to verify origin.
    """
    secret = settings.jwt_secret or settings.encryption_key
    if not secret:
        logger.error("Signing secret is not configured")
        return HTMLResponse(
            status_code=500,
            content="""
            <html>
                <head><title>Error</title></head>
                <body style="font-family: sans-serif; background-color: #0d1117; color: #ff7b72; display: flex; justify-content: center; align-items: center; height: 100vh;">
                    <div style="text-align: center; border: 1px solid #ff7b72; padding: 40px; border-radius: 8px; background-color: #161b22;">
                        <h1>⚠️ Configuration Error</h1>
                        <p>Server signing secret is not configured.</p>
                    </div>
                </body>
            </html>
            """
        )

    # Validate HMAC signature
    expected_msg = f"action:{article_id}:{user_id}:{signal}".encode("utf-8")
    expected_token = hmac.new(secret.encode("utf-8"), expected_msg, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(token, expected_token):
        logger.warning(f"Invalid action token signature for feedback on article {article_id}")
        return HTMLResponse(
            status_code=403,
            content="""
            <html>
                <head><title>Forbidden</title></head>
                <body style="font-family: sans-serif; background-color: #0d1117; color: #ff7b72; display: flex; justify-content: center; align-items: center; height: 100vh;">
                    <div style="text-align: center; border: 1px solid #ff7b72; padding: 40px; border-radius: 8px; background-color: #161b22;">
                        <h1>⚠️ Access Denied</h1>
                        <p>Invalid or expired feedback token.</p>
                    </div>
                </body>
            </html>
            """
        )

    signal_mappings = {
        "more_like_this": "Liked",
        "less_like_this": "Disliked",
        "saved": "Saved",
        "dismissed": "Dismissed"
    }
    action_text = signal_mappings.get(signal, "Recorded")

    try:
        supabase.table("user_feedback").insert({
            "article_id": article_id,
            "user_id": user_id,
            "signal": signal
        }).execute()
    except Exception as e:
        logger.error(f"Error saving feedback: {e}")
        return HTMLResponse(
            status_code=500,
            content="""
            <html>
                <head><title>Error</title></head>
                <body style="font-family: sans-serif; background-color: #0d1117; color: #ff7b72; display: flex; justify-content: center; align-items: center; height: 100vh;">
                    <div style="text-align: center; border: 1px solid #ff7b72; padding: 40px; border-radius: 8px; background-color: #161b22;">
                        <h1>⚠️ Failure</h1>
                        <p>Could not save feedback. Please try again later.</p>
                    </div>
                </body>
            </html>
            """
        )

    return HTMLResponse(
        content=f"""
        <html>
            <head>
                <title>Feedback Recorded</title>
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                    body {{
                        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
                        background: radial-gradient(circle at center, #1b2735 0%, #090a0f 100%);
                        color: #c9d1d9;
                        display: flex;
                        justify-content: center;
                        align-items: center;
                        height: 100vh;
                        margin: 0;
                    }}
                    .card {{
                        background: rgba(22, 27, 34, 0.8);
                        backdrop-filter: blur(12px);
                        border: 1px solid rgba(48, 54, 61, 0.8);
                        border-radius: 16px;
                        padding: 48px;
                        text-align: center;
                        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
                        max-width: 450px;
                        width: 90%;
                    }}
                    .icon {{
                        font-size: 64px;
                        margin-bottom: 24px;
                        display: inline-block;
                        animation: pop 0.4s ease-out;
                    }}
                    h1 {{
                        font-size: 28px;
                        margin: 0 0 12px 0;
                        color: #58a6ff;
                        font-weight: 600;
                    }}
                    p {{
                        font-size: 16px;
                        line-height: 1.5;
                        color: #8b949e;
                        margin: 0 0 24px 0;
                    }}
                    .badge {{
                        background-color: rgba(56, 139, 253, 0.15);
                        color: #58a6ff;
                        border: 1px solid rgba(56, 139, 253, 0.4);
                        padding: 6px 16px;
                        border-radius: 20px;
                        font-size: 14px;
                        font-weight: 600;
                        display: inline-block;
                        text-transform: uppercase;
                    }}
                    @keyframes pop {{
                        0% {{ transform: scale(0.5); }}
                        100% {{ transform: scale(1); }}
                    }}
                </style>
            </head>
            <body>
                <div class="card">
                    <span class="icon">🚀</span>
                    <h1>Feedback Recorded!</h1>
                    <p>We've successfully updated your feed parameters. TechPulse will customize future digests based on this input.</p>
                    <div class="badge">{action_text}</div>
                </div>
            </body>
        </html>
        """
    )

