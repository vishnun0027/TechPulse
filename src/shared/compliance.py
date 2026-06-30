"""
techpulse - compliance
Utility for PII scrubbing and data classification.
"""
import re
from typing import Tuple, List

# Regex patterns for detecting common PII types
EMAIL_REGEX = re.compile(r'[\w\.-]+@[\w\.-]+\.\w+')
PHONE_REGEX = re.compile(r'\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b')
API_KEY_REGEX = re.compile(r'\b(?:api_key|apikey|secret|passwd|password)\b\s*[:=]\s*[\'"]?([a-zA-Z0-9_\-\.]{12,})[\'"]?', re.IGNORECASE)

# Keywords indicating confidential or proprietary information
CONFIDENTIAL_KEYWORDS = ["confidential", "proprietary", "internal only", "non-disclosure", "nda", "restricted access"]


def scrub_pii(text: str) -> Tuple[str, str, List[str]]:
    """
    Scrubs PII entities (email, phone, secrets) from the given text.

    Args:
        text: Raw text to scan and scrub.

    Returns:
        Tuple[str, str, List[str]]:
            - scrubbed_text: Text with PII replaced by placeholders.
            - status: 'clean' (no PII found) or 'scrubbed' (PII removed).
            - entities_found: List of entity types identified (e.g. ['EMAIL', 'PHONE', 'SECRET']).
    """
    if not text:
        return "", "clean", []

    scrubbed = text
    entities = []

    # 1. Emails
    if EMAIL_REGEX.search(scrubbed):
        scrubbed = EMAIL_REGEX.sub("[EMAIL]", scrubbed)
        entities.append("EMAIL")

    # 2. Phone Numbers
    if PHONE_REGEX.search(scrubbed):
        scrubbed = PHONE_REGEX.sub("[PHONE]", scrubbed)
        entities.append("PHONE")

    # 3. Secrets / API Keys
    if API_KEY_REGEX.search(scrubbed):
        scrubbed = API_KEY_REGEX.sub(lambda m: m.group(0).replace(m.group(1), "[SECRET]"), scrubbed)
        entities.append("SECRET")

    status = "scrubbed" if entities else "clean"
    return scrubbed, status, entities


def classify_content(text: str) -> str:
    """
    Determines content classification level based on keywords.

    Args:
        text: Content to analyze.

    Returns:
        str: 'confidential', 'restricted', or 'public'.
    """
    if not text:
        return "public"
    text_lower = text.lower()
    if any(kw in text_lower for kw in CONFIDENTIAL_KEYWORDS):
        return "confidential"
    return "public"
