"""Choose how billing emails are read, set by EXTRACTION_MODE.

- regex  (default): rule-based, no API calls.
- hybrid: regex first; Gemini only for emails regex could not read.
- gemini: Gemini for every email (the original behaviour; uses one API call per email).
"""
from __future__ import annotations

from config import settings
from extraction.schema import BillingEvent

MODES = ("regex", "hybrid", "gemini")


def extract_event(message: dict, mode: str | None = None) -> BillingEvent | None:
    mode = (mode or settings.extraction_mode).lower()
    if mode not in MODES:
        raise ValueError(f"EXTRACTION_MODE must be one of {MODES}, not {mode!r}")
    if mode != "gemini":
        from extraction.regex_extractor import extract_billing_event as regex_extract
        event = regex_extract(message["body"], message_id=message["message_id"], subject=message.get("subject", ""),
                              sender=message.get("sender", ""), received_date=message.get("received_date"))
        if event is not None or mode == "regex":
            return event
    from extraction.llm_extractor import extract_billing_event as gemini_extract
    return gemini_extract(message["body"], message_id=message["message_id"])
