from datetime import datetime, timedelta

import pytest

import notify.notifier as notifier
from notify.notifier import AlertNotice, describe_alert
from rules.trigger_engine import Alert, AlertReason, BillingRecord

ALERT = Alert(AlertReason.PRICE_HIKE, "1")


def notice(merchant="Acme"):
    return AlertNotice(merchant, ALERT, "$9.99 to $12.99 a month")


class FakeNotifier:
    """Records sent notifications and presses the button named in ``press`` (or nothing)."""
    press: list[str | None] = []
    sent: list[dict] = []

    def __init__(self, app_name):
        pass

    async def send(self, title, message, urgency, buttons):
        FakeNotifier.sent.append({"title": title, "message": message, "buttons": [button.title for button in buttons]})
        choice = FakeNotifier.press.pop(0) if FakeNotifier.press else None
        for button in buttons:
            if button.title == choice:
                button.on_pressed()


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(notifier, "DesktopNotifier", FakeNotifier)
    FakeNotifier.press, FakeNotifier.sent = [], []
    return FakeNotifier


def test_notification_has_details_and_both_buttons(fake):
    notifier.notify_alerts([notice()], lambda chosen: None, timeout=0.1)
    assert fake.sent == [{"title": "Acme: price went up", "message": "$9.99 to $12.99 a month",
                          "buttons": ["Cancel subscription", "Do nothing"]}]


def test_cancel_button_starts_agent_after_loop(fake):
    fake.press = ["Cancel subscription"]
    cancelled = []
    assert notifier.notify_alerts([notice()], cancelled.append, timeout=5) == notice()
    assert cancelled == [notice()]


def test_do_nothing_button_never_starts_agent(fake):
    fake.press = ["Do nothing"]
    cancelled = []
    assert notifier.notify_alerts([notice()], cancelled.append, timeout=5) is None
    assert cancelled == []


def test_no_answer_times_out_without_starting_agent(fake):
    cancelled = []
    assert notifier.notify_alerts([notice()], cancelled.append, timeout=0.1) is None
    assert cancelled == []


def test_cancel_on_second_of_two_notices(fake):
    fake.press = ["Do nothing", "Cancel subscription"]
    cancelled = []
    notifier.notify_alerts([notice("Acme"), notice("Spotify")], cancelled.append, timeout=5)
    assert [chosen.merchant_name for chosen in cancelled] == ["Spotify"]


def history(amounts, start=datetime(2026, 1, 1)):
    return [BillingRecord("1", amount, start + timedelta(days=30 * index)) for index, amount in enumerate(amounts)]


def test_describe_price_hike():
    text = describe_alert(Alert(AlertReason.PRICE_HIKE, "1"), history([9.99, 9.99, 12.99]))
    assert text == "$9.99 to $12.99 a month (+30%), now $155.88 a year. Next charge about 1 Apr 2026."


def test_describe_dormant():
    text = describe_alert(Alert(AlertReason.DORMANT, "1"), history([10.99] * 6), "EUR")
    assert text == "€10.99 a month for 6 months with no sign of use (€65.94 so far). Next charge about 30 Jun 2026."


def test_describe_trial_convert_with_unknown_currency():
    text = describe_alert(Alert(AlertReason.TRIAL_CONVERT, "1"), history([14.0]), "CAD")
    assert text == "Trial ended: charged 14.00 CAD on 1 Jan 2026, 168.00 CAD a year if kept. Next charge about 31 Jan 2026."
