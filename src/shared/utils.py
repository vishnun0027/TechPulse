import re
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

_HTML_TAG_RE = re.compile(r"<[^>]+>")

TRACKING_PARAMS_BLACKLIST = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "ref",
    "referrer",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
    "ncid",
    "ref_src",
    "ref_url",
    "_hsenc",
    "_hsmi",
    "mkt_tok",
}


def normalize_url(url: str) -> str:
    """
    Cleans a URL by removing common tracking parameters and normalizes the scheme/host.
    This ensures that the same article from different sources or with tracking
    is treated as the same URL.
    """
    if not url:
        return ""

    parsed = urlparse(url.strip())

    # Lowercase scheme and netloc
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    query_params = parse_qsl(parsed.query)
    filtered_params = [(k, v) for k, v in query_params if k.lower() not in TRACKING_PARAMS_BLACKLIST]

    # Sort params to ensure consistency
    filtered_params.sort()

    new_query = urlencode(filtered_params)

    # Reconstruct URL without fragment and with filtered query
    normalized = urlunparse(
        (
            scheme,
            netloc,
            parsed.path,
            parsed.params,
            new_query,
            "",  # Remove fragment
        )
    )

    return normalized


def clean_html(html_content: str) -> str:
    """
    Removes HTML tags and boilerplate from content to provide clean text for LLMs.
    """
    if not html_content:
        return ""
    try:
        from bs4 import BeautifulSoup

        import importlib.util
        if importlib.util.find_spec("lxml") is not None:
            parser = "lxml"
        else:
            parser = "html.parser"

        soup = BeautifulSoup(html_content, parser)

        # Remove script and style elements
        for script in soup(["script", "style"]):
            script.extract()

        # Get text
        text = soup.get_text(separator=" ")

        # Efficiently clean up whitespace and join chunks
        chunks = (phrase.strip() for line in text.splitlines() for phrase in line.split("  ") if phrase.strip())
        return " ".join(chunks)
    except Exception:
        # Fallback to pre-compiled regex if BS4 fails
        return _HTML_TAG_RE.sub("", html_content)
