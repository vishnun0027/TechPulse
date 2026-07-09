from typing import List, Optional
from shared.compliance import scrub_pii, classify_content
from shared.db import log_telemetry

class DLPResult:
    def __init__(self, scrubbed_text: str, status: str, entities_found: List[str], classification: str):
        self.scrubbed_text = scrubbed_text
        self.status = status
        self.entities_found = entities_found
        self.classification = classification

def dlp_scan_and_scrub(text: str, context: str = "unknown", user_id: Optional[str] = None) -> DLPResult:
    """
    Unified entrypoint for DLP scanning and scrubbing.
    Scrubs PII, classifies content, and logs metrics to telemetry if user_id is provided.
    """
    if not text:
        return DLPResult("", "clean", [], "public")

    scrubbed_text, status, entities = scrub_pii(text)
    classification = classify_content(text)

    # Log to telemetry if PII was scrubbed
    if status == "scrubbed" and user_id:
        try:
            log_telemetry(
                service="dlp_scanner",
                metrics={
                    "pii_scrubbed_count": len(entities),
                    "is_confidential": 1 if classification == "confidential" else 0
                },
                user_id=user_id,
                success=True
            )
        except Exception:
            pass # Non-blocking telemetry failure

    return DLPResult(scrubbed_text, status, entities, classification)
