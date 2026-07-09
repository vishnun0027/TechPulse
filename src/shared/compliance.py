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

# Additional high-risk PII and secret patterns
SSN_REGEX = re.compile(r'\b\d{3}-\d{2}-\d{4}\b')
CREDIT_CARD_REGEX = re.compile(r'\b(?:4[0-9]{12}(?:[0-9]{3})?|(?:5[1-5][0-9]{2}|222[1-9]|22[3-9][0-9]|2[3-6][0-9]{2}|27[0-1][0-9]|2720)[0-9]{12}|3[47][0-9]{13}|3(?:0[0-5]|[68][0-9])[0-9]{11}|6(?:011|5[0-9]{2})[0-9]{12}|(?:2131|1800|35\d{3})\d{11})\b')
IP_REGEX = re.compile(r'\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b')
AWS_KEY_REGEX = re.compile(r'\b(AKIA[0-9A-Z]{16})\b')
GH_TOKEN_REGEX = re.compile(r'\b(gh[ps]_[A-Za-z0-9_]{36,})\b')
PRIVATE_KEY_REGEX = re.compile(r'-----BEGIN (?:RSA |EC |PEM |DSA )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |PEM |DSA )?PRIVATE KEY-----', re.IGNORECASE)

# Keywords indicating confidential or proprietary information
CONFIDENTIAL_KEYWORDS = ["confidential", "proprietary", "internal only", "non-disclosure", "nda", "restricted access"]


def scrub_pii(text: str) -> Tuple[str, str, List[str]]:
    """
    Scrubs PII entities (email, phone, secrets, SSN, Credit Card, IP, API keys) from the given text.

    Args:
        text: Raw text to scan and scrub.

    Returns:
        Tuple[str, str, List[str]]:
            - scrubbed_text: Text with PII replaced by placeholders.
            - status: 'clean' (no PII found) or 'scrubbed' (PII removed).
            - entities_found: List of entity types identified.
    """
    if not text:
        return "", "clean", []

    scrubbed = text
    entities = []

    # 1. Private keys
    if PRIVATE_KEY_REGEX.search(scrubbed):
        scrubbed = PRIVATE_KEY_REGEX.sub("[PRIVATE_KEY]", scrubbed)
        entities.append("PRIVATE_KEY")

    # 2. Emails
    if EMAIL_REGEX.search(scrubbed):
        scrubbed = EMAIL_REGEX.sub("[EMAIL]", scrubbed)
        entities.append("EMAIL")

    # 3. Phone Numbers
    if PHONE_REGEX.search(scrubbed):
        scrubbed = PHONE_REGEX.sub("[PHONE]", scrubbed)
        entities.append("PHONE")

    # 4. SSNs
    if SSN_REGEX.search(scrubbed):
        scrubbed = SSN_REGEX.sub("[SSN]", scrubbed)
        entities.append("SSN")

    # 5. Credit Cards
    if CREDIT_CARD_REGEX.search(scrubbed):
        scrubbed = CREDIT_CARD_REGEX.sub("[CREDIT_CARD]", scrubbed)
        entities.append("CREDIT_CARD")

    # 6. AWS Keys
    if AWS_KEY_REGEX.search(scrubbed):
        scrubbed = AWS_KEY_REGEX.sub("[AWS_KEY]", scrubbed)
        entities.append("AWS_KEY")

    # 7. GitHub Tokens
    if GH_TOKEN_REGEX.search(scrubbed):
        scrubbed = GH_TOKEN_REGEX.sub("[GH_TOKEN]", scrubbed)
        entities.append("GH_TOKEN")

    # 8. Secrets / API Keys
    if API_KEY_REGEX.search(scrubbed):
        scrubbed = API_KEY_REGEX.sub(lambda m: m.group(0).replace(m.group(1), "[SECRET]"), scrubbed)
        entities.append("SECRET")

    # 9. IP Addresses (excluding typical public CDN/DNS like 8.8.8.8 if needed, but standard scrub is safer)
    if IP_REGEX.search(scrubbed):
        # Optional: bypass standard DNS, but safer to scrub all
        scrubbed = IP_REGEX.sub("[IP_ADDRESS]", scrubbed)
        entities.append("IP_ADDRESS")

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
