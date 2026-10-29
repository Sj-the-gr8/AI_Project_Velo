"""Cancel subscription from the dashboard: the one-at-a-time lock, the background script, and the button itself."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from agent import run_lock
from agent.run_lock import AgentBusy, agent_lock
from db.db import Database
from scripts import cancel_subscription, run_pipeline_once
from tests.test_pipeline import FakeGmail, fake_extractor, message


def test_lock_allows_one_run_at_a_time_and_releases(tmp_path):
    db = tmp_path / "velo.db"
    with agent_lock(db, "Acme"):
        assert run_lock.current(db)["merchant"] == "Acme"
        with pytest.raises(AgentBusy):
            with agent_lock(db, "Tunely"):
                pass
    assert run_lock.current(db) is None
    with agent_lock(db, "Tunely"):  # free again
        pass


def test_stale_lock_from_a_crash_is_ignored(tmp_path):
    db = tmp_path / "velo.db"
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    run_lock.lock_path(db).write_text(json.dumps({"merchant": "Crashed", "started": old}), encoding="utf-8")
    assert run_lock.current(db) is None
    with agent_lock(db, "Acme"):
        assert run_lock.current(db)["merchant"] == "Acme"


def seed_alert(test_settings):
    gmail = FakeGmail([message("m1", 1, "Acme|9.99|2026-01-01"), message("m2", 2, "Acme|12.99|2026-01-31")])
    run_pipeline_once.run(client=gmail, extractor=fake_extractor, notify=False)
    with sqlite3.connect(test_settings.database_path) as connection:
        return connection.execute("SELECT id FROM alerts").fetchone()[0]


def test_notice_for_alert_rebuilds_the_notification(test_settings):
    alert_id = seed_alert(test_settings)
    notice = run_pipeline_once.notice_for_alert(Database(test_settings.database_path), alert_id)
    assert notice.merchant_name == "Acme" and notice.alert.reason.value == "price_hike"
    assert notice.details.startswith("$9.99 to $12.99 a month")


def test_cancel_script_runs_the_agent_and_skips_closed_alerts(test_settings, monkeypatch):
    alert_id = seed_alert(test_settings)
    started = []
    monkeypatch.setattr(cancel_subscription, "start_review_agent", lambda notice: started.append(notice.merchant_name) or "success")
    assert cancel_subscription.main(["--alert-id", str(alert_id)]) == 0 and started == ["Acme"]
    Database(test_settings.database_path).mark_cancelled(1)
    assert cancel_subscription.main(["--alert-id", str(alert_id)]) == 1 and started == ["Acme"]  # alert now resolved


def test_second_cancellation_is_refused_while_one_runs(test_settings, monkeypatch):
    import agent.vision_agent
    alert_id = seed_alert(test_settings)
    notice = run_pipeline_once.notice_for_alert(Database(test_settings.database_path), alert_id)
    monkeypatch.setattr(agent.vision_agent, "run_agent", lambda start_url=None: pytest.fail("agent should not start"))
    with agent_lock(test_settings.database_path, "Tunely"):
        assert run_pipeline_once.start_review_agent(notice) is None


def test_dashboard_button_confirms_then_launches_the_cancel_script(test_settings, monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest
    import subprocess

    alert_id = seed_alert(test_settings)
    monkeypatch.setenv("DATABASE_PATH", str(test_settings.database_path))
    launched = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            launched.append((args, kwargs["env"]["DATABASE_PATH"]))

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    from dashboard import report
    monkeypatch.setattr(report, "fetch_rates", lambda timeout=4.0: report.Rates(dict(report.FALLBACK_PER_USD), report.FALLBACK_DATE, False))
    app = AppTest.from_file("../dashboard/app.py", default_timeout=30)
    app.run()
    assert not app.exception
    confirm = app.button(key=f"confirm-{alert_id}")
    confirm.click().run()
    assert not app.exception
    (args, database), = launched
    assert args[-2:] == ["--alert-id", str(alert_id)] and args[1].endswith("cancel_subscription.py")
    assert database == str(test_settings.database_path.resolve())
    # While it runs, every Cancel subscription button is disabled.
    assert app.button(key=f"cancel-{alert_id}").disabled
