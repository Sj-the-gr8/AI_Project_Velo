from __future__ import annotations

import base64
from datetime import datetime, timezone
from email import policy
from email.message import Message
from email.parser import BytesParser
from html import unescape
import re


def _strip_html(value: str) -> str:
    value = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", unescape(value)).strip()


def extract_plain_text(message: Message) -> str:
    parts = message.walk() if message.is_multipart() else [message]
    plain = []
    html = []
    for part in parts:
        if part.get_content_disposition() == "attachment":
            continue
        content = part.get_payload(decode=True)
        if content is None:
            continue
        text = content.decode(part.get_content_charset() or "utf-8", errors="replace")
        if part.get_content_type() == "text/plain":
            plain.append(text)
        elif part.get_content_type() == "text/html":
            html.append(text)
    return "\n".join(plain).strip() or _strip_html("\n".join(html))


def parse_gmail_message(resource: dict) -> dict:
    """Parse a users.messages.get(format="raw") resource into id, received date, headers and plain text."""
    message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(resource["raw"]))
    internal_date = int(resource["internalDate"])
    return {
        "message_id": resource["id"],
        "internal_date": internal_date,
        "received_date": datetime.fromtimestamp(internal_date / 1000, timezone.utc).isoformat(),
        "subject": str(message.get("Subject", "")),
        "sender": str(message.get("From", "")),
        "body": extract_plain_text(message),
    }
