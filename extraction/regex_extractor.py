"""Rule-based billing extraction: deterministic, offline, and free (no Gemini calls).

Pulls merchant, amount, currency, billing date, and trial status out of a receipt email with regular
expressions. Anything it cannot read confidently is skipped rather than guessed, the same contract the
Gemini extractor follows, so the rest of the pipeline does not care which extractor produced an event.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import parseaddr

from pydantic import ValidationError

from extraction.schema import BillingEvent, Merchant

logger = logging.getLogger(__name__)

# --- Money -------------------------------------------------------------------------------------------

CODES = "USD|EUR|GBP|INR|CAD|AUD|JPY|SGD|AED"
SYMBOLS = {"$": "USD", "US$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "RS": "INR", "RS.": "INR",
           "CA$": "CAD", "C$": "CAD", "A$": "AUD", "AU$": "AUD"}
NUMBER = r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?"
MONEY = re.compile(
    rf"(?P<prefix>US\$|CA\$|AU\$|C\$|A\$|\$|€|£|₹|\b(?:Rs\.?|{CODES})(?=\s?\d))\s?(?P<amount>{NUMBER})(?:\s?(?P<suffix>{CODES})\b)?"
    rf"|\b(?P<bare>{NUMBER})\s?(?P<code>{CODES})\b",
    re.IGNORECASE,
)
# A sentence that says money was taken...
CHARGE_WORDS = re.compile(
    r"\b(charged|charge of|amount (?:charged|paid|due)|total|paid|payment|billed|receipt|invoice|renewed|subscription fee)\b",
    re.IGNORECASE,
)
# ...versus one that offers, promises, or refunds money.
NOT_A_CHARGE = re.compile(
    r"\b(save|saving|discount|offer|instead|only|will be|upcoming|starting|refund(?:ed)?|credit(?:ed)?|keep)\b",
    re.IGNORECASE,
)

# --- Dates -------------------------------------------------------------------------------------------

MONTHS = {name: index for index, names in enumerate(
    [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"),
     ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
     ("dec", "december")], start=1) for name in names}
MONTH = r"(?P<mon>" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?"
DATE_PATTERNS = [
    re.compile(rf"\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+{MONTH}\s*,?\s+(?P<y>\d{{4}})\b", re.IGNORECASE),   # 1 July 2026
    re.compile(rf"\b{MONTH}\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s+(?P<y>\d{{4}})\b", re.IGNORECASE),   # July 1, 2026
    re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})\b"),                                          # 2026-07-01
    re.compile(r"\b(?P<a>\d{1,2})[/.](?P<b>\d{1,2})[/.](?P<y>\d{4})\b"),                                     # 01/07/2026
]
# Words just before a date that make it a future or validity date, not the date of this charge.
FUTURE_CONTEXT = re.compile(r"\b(next|renews?|renewal|upcoming|due|will|until|through|expires?|starting)\b[^.]*$", re.IGNORECASE)

# --- Merchant ----------------------------------------------------------------------------------------

NAME = r"(?P<name>[A-Z0-9][\w&'+-]*(?:\s+[A-Z0-9][\w&'+-]*){0,4})"
SUBJECT_PATTERNS = [
    re.compile(r"(?i:receipt|invoice|payment|order|bill)\s+(?i:from)\s+" + NAME),          # Your receipt from Apple
    re.compile(r"^(?i:your\s+)?" + NAME + r"\s+(?i:receipt|invoice|payment|billing|subscription|membership|order)\b"),  # Acme Video receipt
    re.compile(r"^" + NAME + r"\s*[:|–-]\s"),                                                   # Netflix: your payment...
]
BODY_PATTERNS = [
    re.compile(r"(?i:thanks|thank you)\s+(?i:for)\s+(?i:your|choosing|subscribing to)\s+" + NAME
               + r"\s+(?i:subscription|membership|plan|order|purchase|payment)\b"),
    re.compile(r"\b(?i:your)\s+" + NAME + r"\s+(?i:free trial|trial|subscription|membership|plan|receipt|invoice|order)\b"),
    re.compile(r"(?i:subscription to|subscribed to|payment to)\s+" + NAME),
]
GENERIC_WORDS = {"your", "the", "a", "an", "receipt", "invoice", "payment", "order", "thanks", "thank", "you", "hi",
                 "hello", "dear", "subscription", "membership", "account", "billing", "new", "important", "reminder",
                 "re", "fwd", "fw", "team", "support", "no-reply", "noreply", "notifications", "info"}
PLAN_SUFFIX = re.compile(r"(?:\s+(?:Premium|Plus|Pro|Basic|Standard|Family|Individual|Student|Duo|Monthly|Annual|Yearly|Plan))+$")
PERSONAL_MAIL_DOMAINS = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com",
                         "icloud.com", "me.com", "proton.me", "protonmail.com", "aol.com"}

TRIAL = re.compile(r"\btrial\b", re.IGNORECASE)
TRIAL_ENDED = re.compile(r"\b(ended|expired|converted|is over|now paid|first (?:payment|charge))\b", re.IGNORECASE)


@dataclass(frozen=True)
class _Money:
    amount: Decimal
    currency: str
    sentence: str
    score: int


def _sentences(text: str) -> list[str]:
    # Split on sentence ends and line breaks, but not inside "Rs. 499".
    return [part.strip() for part in re.split(r"(?<!Rs\.)(?<=[.!?])\s+|\n+", text) if part.strip()]


def _money_mentions(text: str) -> list[_Money]:
    mentions = []
    for sentence in _sentences(text):
        score = (2 if CHARGE_WORDS.search(sentence) else 0) - (3 if NOT_A_CHARGE.search(sentence) else 0)
        for match in MONEY.finditer(sentence):
            raw = (match.group("amount") or match.group("bare")).replace(",", "")
            code = match.group("suffix") or match.group("code")
            prefix = (match.group("prefix") or "").strip().upper()
            currency = (code or SYMBOLS.get(prefix) or prefix).upper()
            try:
                mentions.append(_Money(Decimal(raw), currency, sentence, score))
            except InvalidOperation:
                continue
    return mentions


def _dates_in(sentence: str) -> list[tuple[date, bool]]:
    """Return (date, is_future_context) for every parseable date in a sentence."""
    found = []
    for pattern in DATE_PATTERNS:
        for match in pattern.finditer(sentence):
            groups = match.groupdict()
            try:
                if groups.get("mon"):
                    value = date(int(groups["y"]), MONTHS[groups["mon"].lower().rstrip(".")], int(groups["d"]))
                elif groups.get("a"):
                    first, second = int(groups["a"]), int(groups["b"])
                    # Unambiguous when one part exceeds 12; otherwise read as day/month (the Indian convention).
                    day, month = (second, first) if second > 12 else (first, second)
                    value = date(int(groups["y"]), month, day)
                else:
                    value = date(int(groups["y"]), int(groups["m"]), int(groups["d"]))
            except (ValueError, KeyError):
                continue
            found.append((value, bool(FUTURE_CONTEXT.search(sentence[:match.start()]))))
    return found


def _clean_name(candidate: str) -> str | None:
    name = PLAN_SUFFIX.sub("", candidate.strip(" .,-:")).strip()
    words = name.lower().split()
    if not words or words[0] in GENERIC_WORDS or all(word in GENERIC_WORDS for word in words) or len(name) < 2:
        return None
    return name


def _merchant(subject: str, body: str, sender: str) -> tuple[str | None, str]:
    subject = re.sub(r"^(?:(?:re|fwd?)\s*:\s*)+", "", subject.strip(), flags=re.IGNORECASE)
    for source, patterns, text in (("subject", SUBJECT_PATTERNS, subject), ("body", BODY_PATTERNS, body)):
        for pattern in patterns:
            for match in pattern.finditer(text):
                if name := _clean_name(match.group("name")):
                    return name, source
    display, address = parseaddr(sender)
    domain = address.rpartition("@")[2].lower()
    if domain and domain not in PERSONAL_MAIL_DOMAINS:
        if display and (name := _clean_name(re.sub(r"\b(billing|payments?|receipts?|team|no-?reply)\b", "", display, flags=re.I))):
            return name, "sender"
        label = domain.split(".")[-2] if domain.count(".") >= 1 else domain
        return label.capitalize(), "sender"
    return None, ""


def _received(received_date: str | None) -> date | None:
    if not received_date:
        return None
    try:
        return datetime.fromisoformat(received_date).date()
    except ValueError:
        return None


def extract_billing_event(body: str, message_id: str | None = None, subject: str = "", sender: str = "",
                          received_date: str | None = None) -> BillingEvent | None:
    """Extract one billing event, or None (logged) when the email is not a readable receipt."""
    def skip(reason: str) -> None:
        logger.info("Regex extractor skipped message %s: %s", message_id, reason)

    text = f"{subject}\n{body}" if subject else body
    mentions = _money_mentions(text)
    if not mentions:
        return skip("no amount found")
    best = max(mentions, key=lambda mention: mention.score)  # max keeps the first of equal scores
    if best.score <= 0:
        distinct = {mention.amount for mention in mentions}
        if not (CHARGE_WORDS.search(text) and len(distinct) == 1 and best.score == 0):
            return skip("no amount in a sentence that describes a charge")

    merchant, merchant_source = _merchant(subject, body, sender)
    if merchant is None:
        return skip("no merchant name found")

    received = _received(received_date)
    latest_allowed = (received or date.today()) + timedelta(days=2)
    candidates = [(value, future) for value, future in _dates_in(best.sentence)]
    candidates += [item for sentence in _sentences(text) if sentence != best.sentence for item in _dates_in(sentence)]
    past = [value for value, future in candidates if not future and value <= latest_allowed]
    upcoming = [value for value, future in candidates if future]
    billing_date = past[0] if past else received
    if billing_date is None:
        return skip("no billing date found")

    confidence = 0.5 + (0.2 if best.score > 0 else 0) + (0.2 if past else 0) + (0.1 if merchant_source != "sender" else 0)
    try:
        return BillingEvent(
            merchant=Merchant(name=merchant, canonical_id=merchant),
            amount=best.amount,
            currency=best.currency,
            billing_date=billing_date,
            next_billing_date_guess=next((value for value in upcoming if value > billing_date), None),
            confidence=round(min(confidence, 1.0), 2),
            trial_converted=bool(TRIAL.search(text) and TRIAL_ENDED.search(text)),
            raw_text=body[:2000],
        )
    except ValidationError as error:
        return skip(f"failed validation: {error.errors()[0]['msg']}")
