"""Do nothing (notification) and Keep subscription (dashboard) close an alert as "kept"."""
import sqlite3

import notify.notifier as notifier
from db.db import Database
from notify.notifier import AlertNotice
from rules.trigger_engine import Alert, AlertReason
from scripts import run_pipeline_once
from tests.test_notifier import FakeNotifier
from tests.test_pipeline import FakeGmail, fake_extractor, message


def alerts(path):
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT reason, resolved, resolution, price FROM alerts ORDER BY id").fetchall()


def test_do_nothing_calls_on_keep_and_never_cancels(monkeypatch):
    monkeypatch.setattr(notifier, "DesktopNotifier", FakeNotifier)
    FakeNotifier.press, FakeNotifier.sent = ["Do nothing", "Cancel subscription"], []
    kept, cancelled = [], []
    acme = AlertNotice("Acme", Alert(AlertReason.PRICE_HIKE, "1"), "x", 1)
    tunely = AlertNotice("Tunely", Alert(AlertReason.DORMANT, "2"), "y", 2)
    notifier.notify_alerts([acme, tunely], cancelled.append, timeout=5, on_keep=kept.append)
    assert kept == [acme] and cancelled == [tunely]


def test_no_answer_closes_nothing(monkeypatch):
    monkeypatch.setattr(notifier, "DesktopNotifier", FakeNotifier)
    FakeNotifier.press, FakeNotifier.sent = [], []
    kept = []
    notifier.notify_alerts([AlertNotice("Acme", Alert(AlertReason.PRICE_HIKE, "1"), "x", 1)], lambda notice: None,
                           timeout=0.1, on_keep=kept.append)
    assert kept == []


def test_kept_alert_closes_and_is_not_raised_again_at_the_same_price(test_settings, monkeypatch):
    db = test_settings.database_path
    gmail = FakeGmail([message("p1", 1, "PixelPlay|7.99|2026-08-14"), message("p2", 2, "PixelPlay|10.99|2026-09-13")])
    notice, = run_pipeline_once.run(client=gmail, extractor=fake_extractor, notify=False)
    run_pipeline_once.keep_alert(notice)
    assert alerts(db) == [("price_hike", 1, "kept", 10.99)]
    # Whenever the rules report the same alert again at the same price, the user's choice to keep it stands.
    monkeypatch.setattr(run_pipeline_once, "evaluate_subscription", lambda history: Alert(AlertReason.PRICE_HIKE, "1"))
    gmail.messages.append(message("p3", 3, "PixelPlay|10.99|2026-10-13"))
    assert run_pipeline_once.run(client=gmail, extractor=fake_extractor, notify=False) == []
    # At a new price it is new information, so it raises a fresh alert.
    gmail.messages.append(message("p4", 4, "PixelPlay|12.99|2026-11-12"))
    fresh, = run_pipeline_once.run(client=gmail, extractor=fake_extractor, notify=False)
    assert fresh.merchant_name == "PixelPlay" and alerts(db)[-1] == ("price_hike", 0, None, 12.99)


def test_watcher_passes_do_nothing_through_to_the_database(test_settings, monkeypatch):
    def press_do_nothing(notices, on_cancel, timeout, on_keep):
        for notice in notices:
            on_keep(notice)
        return None

    monkeypatch.setattr(run_pipeline_once, "notify_alerts", press_do_nothing)
    gmail = FakeGmail([message("p1", 1, "PixelPlay|7.99|2026-08-14"), message("p2", 2, "PixelPlay|10.99|2026-09-13")])
    run_pipeline_once.run(client=gmail, extractor=fake_extractor)
    assert alerts(test_settings.database_path) == [("price_hike", 1, "kept", 10.99)]


def test_cancelling_records_the_resolution(test_settings):
    gmail = FakeGmail([message("p1", 1, "PixelPlay|7.99|2026-08-14"), message("p2", 2, "PixelPlay|10.99|2026-09-13")])
    run_pipeline_once.run(client=gmail, extractor=fake_extractor, notify=False)
    Database(test_settings.database_path).mark_cancelled(1)
    assert alerts(test_settings.database_path) == [("price_hike", 1, "cancelled", 10.99)]


def test_dashboard_keep_button_closes_the_alert(test_settings, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from dashboard import report

    monkeypatch.setattr(report, "fetch_rates", lambda timeout=4.0: report.Rates(dict(report.FALLBACK_PER_USD), report.FALLBACK_DATE, False))
    gmail = FakeGmail([message("p1", 1, "PixelPlay|7.99|2026-08-14"), message("p2", 2, "PixelPlay|10.99|2026-09-13")])
    run_pipeline_once.run(client=gmail, extractor=fake_extractor, notify=False)
    app = AppTest.from_file("../dashboard/app.py", default_timeout=30)
    app.run()
    app.button(key="keep-1").click().run()
    assert not app.exception
    assert alerts(test_settings.database_path) == [("price_hike", 1, "kept", 10.99)]
    assert any("No open alerts." in element.value for element in app.success)
