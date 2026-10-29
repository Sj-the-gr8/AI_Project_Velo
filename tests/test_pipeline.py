import sqlite3
from datetime import date
from decimal import Decimal

from extraction.schema import BillingEvent, Merchant
from scripts import run_pipeline_once


class FakeGmail:
    def __init__(self, messages):
        self.messages = messages
        self.cursor = None

    def fetch_billing_emails(self, since=None):
        return [m for m in sorted(self.messages, key=lambda m: m["internal_date"]) if self.cursor is None or m["internal_date"] > self.cursor]

    def save_cursor(self, internal_date):
        self.cursor = internal_date


def message(message_id, internal_date, body):
    return {"message_id": message_id, "internal_date": internal_date, "received_date": "2026-01-01T00:00:00+00:00", "body": body}


def fake_extractor(message):
    # body format: "merchant|amount|YYYY-MM-DD", or "junk" for a non-billing email
    body = message["body"]
    if body == "junk":
        return None
    merchant, amount, billing_date = body.split("|")
    return BillingEvent(merchant=Merchant(name=merchant, canonical_id=merchant), amount=Decimal(amount), currency="USD",
                        billing_date=date.fromisoformat(billing_date), confidence=0.9, raw_text=body)


def rows(path, query):
    with sqlite3.connect(path) as connection:
        return connection.execute(query).fetchall()


def test_pipeline_stores_events_raises_alert_and_is_idempotent(test_settings):
    gmail = FakeGmail([
        message("m3", 3, "Acme|12.99|2026-03-01"),
        message("m1", 1, "Acme|9.99|2026-01-01"),
        message("m2", 2, "Acme|9.99|2026-01-31"),
        message("m4", 4, "junk"),
        message("m5", 5, "Other|5.00|2026-03-01"),
    ])
    alerts = run_pipeline_once.run(client=gmail, extractor=fake_extractor, notify=False)

    assert [(notice.merchant_name, notice.alert.reason.value) for notice in alerts] == [("Acme", "price_hike")]
    assert alerts[0].details == "$9.99 to $12.99 a month (+30%), now $155.88 a year. Next charge about 31 Mar 2026."
    assert gmail.cursor == 5
    db = test_settings.database_path
    assert rows(db, "SELECT merchant_name, current_amount, first_seen, last_seen FROM subscriptions ORDER BY merchant_name") == [
        ("Acme", 12.99, "2026-01-01", "2026-03-01"), ("Other", 5.0, "2026-03-01", "2026-03-01")]
    assert rows(db, "SELECT COUNT(*) FROM billing_events") == [(4,)]
    assert rows(db, "SELECT reason FROM alerts") == [("price_hike",)]

    # A second run over the same mail (cursor reset) must not duplicate rows or alerts.
    gmail.cursor = None
    assert run_pipeline_once.run(client=gmail, extractor=fake_extractor, notify=False) == []
    assert rows(db, "SELECT COUNT(*) FROM billing_events") == [(4,)]
    assert rows(db, "SELECT COUNT(*) FROM alerts") == [(1,)]


def test_older_mail_does_not_overwrite_current_amount(test_settings):
    newest_first = FakeGmail([])
    newest_first.fetch_billing_emails = lambda since=None: [message("b", 2, "Acme|12.99|2026-02-01"), message("a", 1, "Acme|9.99|2026-01-01")]
    run_pipeline_once.run(client=newest_first, extractor=fake_extractor, notify=False)
    assert rows(test_settings.database_path, "SELECT current_amount, first_seen, last_seen FROM subscriptions") == [(12.99, "2026-01-01", "2026-02-01")]


def test_cancel_choice_hands_off_to_agent(test_settings, monkeypatch):
    calls = []
    monkeypatch.setattr(run_pipeline_once, "notify_alerts", lambda notices, on_cancel, timeout, on_keep=None: on_cancel(notices[0]) or notices[0])
    monkeypatch.setattr(run_pipeline_once, "start_review_agent", lambda notice: calls.append((notice.merchant_name, notice.alert.reason.value)))
    gmail = FakeGmail([message("m1", 1, "Acme|9.99|2026-01-01"), message("m2", 2, "Acme|19.99|2026-01-31")])
    run_pipeline_once.run(client=gmail, extractor=fake_extractor)
    assert calls == [("Acme", "price_hike")]


def run_cancel_flow(test_settings, monkeypatch, outcome):
    import agent.sandbox
    import agent.vision_agent
    from contextlib import nullcontext
    from db.db import Database

    gmail = FakeGmail([message("m1", 1, "Acme|9.99|2026-01-01"), message("m2", 2, "Acme|19.99|2026-01-31")])
    notice = run_pipeline_once.run(client=gmail, extractor=fake_extractor, notify=False)[0]

    def fake_agent(start_url=None):
        Database(test_settings.database_path).log_agent_action("run-1", 1, "final.png", None, "done", outcome)
        return "run-1"

    monkeypatch.setattr(agent.sandbox, "mock_site", nullcontext)
    monkeypatch.setattr(agent.vision_agent, "run_agent", fake_agent)
    run_pipeline_once.start_review_agent(notice)
    return test_settings.database_path


def test_successful_agent_run_marks_subscription_cancelled(test_settings, monkeypatch):
    db = run_cancel_flow(test_settings, monkeypatch, "success")
    assert rows(db, "SELECT status FROM subscriptions") == [("cancelled",)]
    assert rows(db, "SELECT resolved FROM alerts") == [(1,)]


def test_stopped_agent_run_leaves_subscription_active(test_settings, monkeypatch):
    db = run_cancel_flow(test_settings, monkeypatch, "stopped")
    assert rows(db, "SELECT status FROM subscriptions") == [("active",)]
    assert rows(db, "SELECT resolved FROM alerts") == [(0,)]
