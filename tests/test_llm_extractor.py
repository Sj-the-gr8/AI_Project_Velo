import json
import logging
from decimal import Decimal

import pytest

from extraction.llm_extractor import extract_billing_event

VALID = {
    "merchant": {"name": "Netflix", "canonical_id": "Netflix Inc."},
    "amount": "15.49",
    "currency": "usd",
    "billing_date": "2026-01-05",
    "next_billing_date_guess": None,
    "confidence": 0.93,
    "trial_converted": False,
}


class StubResponse:
    def __init__(self, text):
        self.text = text


class StubClient:
    def __init__(self, text):
        self.text = text
        self.prompts = []

    def generate_content(self, prompt):
        self.prompts.append(prompt)
        return StubResponse(self.text)


def test_valid_response_is_parsed_and_normalised():
    event = extract_billing_event("Netflix receipt $15.49", client=StubClient(json.dumps(VALID)))
    assert event.merchant.canonical_id == "netflix-inc"
    assert event.amount == Decimal("15.49")
    assert event.currency == "USD"
    assert event.raw_text == "Netflix receipt $15.49"


def test_fenced_json_is_accepted():
    assert extract_billing_event("body", client=StubClient("```json\n" + json.dumps(VALID) + "\n```")) is not None


def test_body_with_braces_does_not_break_prompt():
    client = StubClient(json.dumps(VALID))
    extract_billing_event("Hi {name}, your total is {amount}", client=client)
    assert client.prompts[0].endswith("Hi {name}, your total is {amount}")


@pytest.mark.parametrize("override", [
    {"amount": "-5"},
    {"amount": "0"},
    {"amount": "5000000"},
    {"merchant": {"name": "   ", "canonical_id": "x"}},
    {"merchant": {"name": "Acme", "canonical_id": "!!!"}},
    {"merchant": None},
    {"billing_date": "not a date"},
    {"currency": "DOLLARS"},
    {"confidence": 1.5},
])
def test_invalid_fields_are_skipped(override, caplog):
    payload = {**VALID, **override}
    with caplog.at_level(logging.WARNING):
        assert extract_billing_event("body", client=StubClient(json.dumps(payload)), message_id="msg-42") is None
    assert "msg-42" in caplog.text


def test_non_json_logs_raw_response_and_message_id(caplog):
    with caplog.at_level(logging.WARNING):
        assert extract_billing_event("body", client=StubClient("Sorry, I cannot help"), message_id="msg-7") is None
    assert "msg-7" in caplog.text
    assert "Sorry, I cannot help" in caplog.text


def test_blocked_response_is_skipped():
    class Blocked:
        @property
        def text(self):
            raise ValueError("response was blocked")

    class Client:
        def generate_content(self, prompt):
            return Blocked()

    assert extract_billing_event("body", client=Client()) is None
