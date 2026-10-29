from datetime import date
from decimal import Decimal

import pytest

from extraction import extractor
from extraction.regex_extractor import extract_billing_event

RECEIVED = "2026-09-24T10:00:00+00:00"
PERSONAL = "Sanchit <someone@gmail.com>"


def extract(subject, body, sender=PERSONAL):
    return extract_billing_event(body, message_id="m", subject=subject, sender=sender, received_date=RECEIVED)


# (subject, body, sender, merchant, amount, currency, billing date, next billing date)
RECEIPTS = [
    ("Acme Video receipt – July", "Thanks for your Acme Video Premium subscription. Receipt: $9.99 USD charged on July 1, 2026.",
     PERSONAL, "Acme Video", "9.99", "USD", date(2026, 7, 1), None),
    ("Acme Video receipt – August", "Thanks for your Acme Video Premium subscription. Receipt: $12.99 USD charged on August 30, 2026. "
     "Your new price is $12.99 per month.", PERSONAL, "Acme Video", "12.99", "USD", date(2026, 8, 30), None),
    ("Tunely receipt – April", "Your Tunely Music subscription receipt: $10.99 USD charged on April 5, 2026.",
     PERSONAL, "Tunely", "10.99", "USD", date(2026, 4, 5), None),
    ("Your receipt from Apple", "Apple ID receipt\nDate: 12 Sep 2026\niCloud+ 50GB monthly  Total ₹75.00\n"
     "Your subscription renews on 12 Oct 2026.", "Apple <no_reply@email.apple.com>", "Apple", "75.00", "INR",
     date(2026, 9, 12), date(2026, 10, 12)),
    ("Your Spotify Premium receipt", "Thanks for your payment.\nAmount paid: Rs. 119\nDate: 03/09/2026\nNext billing date: 03/10/2026",
     "Spotify <no-reply@spotify.com>", "Spotify", "119", "INR", date(2026, 9, 3), date(2026, 10, 3)),
    ("Invoice INV-2207", "Invoice for your Figmo Pro plan. Total due: USD 1,240.00. Billed on 2026-09-01.",
     "Figmo Billing <billing@figmo.example>", "Figmo", "1240.00", "USD", date(2026, 9, 1), None),
    ("Payment confirmation", "Your payment of €7.49 was received on 2 September 2026. Thank you!",
     "StreamCo <billing@streamco.example>", "StreamCo", "7.49", "EUR", date(2026, 9, 2), None),
]


@pytest.mark.parametrize("subject, body, sender, merchant, amount, currency, billed, upcoming", RECEIPTS)
def test_reads_receipts(subject, body, sender, merchant, amount, currency, billed, upcoming):
    event = extract(subject, body, sender)
    assert (event.merchant.name, event.amount, event.currency, event.billing_date, event.next_billing_date_guess) == (
        merchant, Decimal(amount), currency, billed, upcoming)


def test_same_merchant_gets_same_canonical_id_across_emails():
    first = extract(*RECEIPTS[0][:2])
    second = extract(*RECEIPTS[1][:2])
    assert first.merchant.canonical_id == second.merchant.canonical_id == "acme-video"


def test_trial_conversion_is_flagged():
    event = extract("NoteNest receipt – trial ended",
                    "Your NoteNest free trial has ended and converted to a paid subscription. Receipt: $8.00 USD charged on September 20, 2026.")
    assert event.trial_converted and event.merchant.name == "NoteNest"


@pytest.mark.parametrize("subject, body", [
    ("StreamBox fall lineup", "New shows are coming to StreamBox this fall! Manage your subscription preferences anytime."),
    ("Before you go", "Keep Premium for only $9.99/month instead of $19.99. Offer ends soon!"),
    ("Your refund", "We refunded $12.99 to your card on September 3, 2026."),
    ("Reminder", "Your card will be charged $12.99 on October 1, 2026."),
])
def test_non_charges_are_skipped(subject, body):
    assert extract(subject, body) is None


def test_missing_merchant_is_skipped():
    assert extract("Receipt", "Amount charged: $5.00 on July 1, 2026.") is None


def test_falls_back_to_received_date_with_lower_confidence():
    event = extract("Acme Video receipt", "Receipt: $9.99 USD charged today.")
    assert event.billing_date == date(2026, 9, 24)
    assert event.confidence < 1


def test_future_dates_are_not_billing_dates():
    event = extract("Acme Video receipt", "Receipt: $9.99 charged on 1 Sep 2026. Next charge on 1 Oct 2026.")
    assert event.billing_date == date(2026, 9, 1) and event.next_billing_date_guess == date(2026, 10, 1)


def message(body, subject="Acme Video receipt"):
    return {"message_id": "m", "body": body, "subject": subject, "sender": PERSONAL, "received_date": RECEIVED}


def test_regex_mode_never_calls_gemini(monkeypatch):
    import extraction.llm_extractor as llm
    monkeypatch.setattr(llm, "extract_billing_event", lambda *a, **k: pytest.fail("Gemini was called"))
    assert extractor.extract_event(message("Nothing to see"), mode="regex") is None
    assert extractor.extract_event(message("Receipt: $9.99 charged on July 1, 2026."), mode="regex").amount == Decimal("9.99")


def test_hybrid_mode_calls_gemini_only_when_regex_fails(monkeypatch):
    import extraction.llm_extractor as llm
    calls = []
    monkeypatch.setattr(llm, "extract_billing_event", lambda body, message_id=None: calls.append(body))
    extractor.extract_event(message("Receipt: $9.99 charged on July 1, 2026."), mode="hybrid")
    extractor.extract_event(message("Something regex cannot read"), mode="hybrid")
    assert calls == ["Something regex cannot read"]


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        extractor.extract_event(message("x"), mode="magic")
