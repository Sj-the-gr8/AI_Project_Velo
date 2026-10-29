"""With AGENT_SCENARIO=auto, each alerted subscription gets its own sandbox, in turn, and keeps it."""
import dataclasses
from contextlib import nullcontext
from urllib.parse import urlparse

from agent.sandbox import ROTATION
from db.db import Database
from scripts import run_pipeline_once
from tests.test_pipeline import FakeGmail, fake_extractor, message


def sandboxes(path):
    import sqlite3
    with sqlite3.connect(path) as connection:
        return dict(connection.execute("SELECT merchant_name, sandbox FROM subscriptions WHERE sandbox IS NOT NULL"))


def test_rotation_matches_the_sandboxes_the_site_serves():
    from agent.mock_site.app import SCENARIOS
    assert ROTATION == ("classic", *SCENARIOS)


def test_each_alerted_subscription_gets_the_next_sandbox_in_order(test_settings):
    gmail = FakeGmail([])
    def arrive(*mails):
        gmail.messages.extend(mails)
        run_pipeline_once.run(client=gmail, extractor=fake_extractor, notify=False)
    # The demo's order of alerts: Acme first, then Kinora, PixelPlay, PageTurn, FitPulse.
    arrive(message("a1", 1, "Acme|9.99|2026-07-01"), message("a2", 2, "Acme|12.99|2026-07-31"))
    arrive(message("k1", 3, "Kinora|149|2026-08-05"), message("s1", 4, "Steady|5|2026-08-01"))
    arrive(message("k2", 5, "Kinora|199|2026-09-04"))
    arrive(message("p1", 6, "PixelPlay|7.99|2026-08-14"), message("p2", 7, "PixelPlay|10.99|2026-09-13"))
    arrive(*[message(f"t{i}", 10 + i, f"PageTurn|8.99|2026-{4 + i:02d}-20") for i in range(6)])
    assert sandboxes(test_settings.database_path) == {"Acme": "classic", "Kinora": "maze", "PixelPlay": "trick", "PageTurn": "pause_trap"}
    # Subscriptions without an alert get none; a later alert for an assigned subscription keeps its sandbox.
    assert "Steady" not in sandboxes(test_settings.database_path)
    Database(test_settings.database_path).sandbox_for(1, ROTATION)
    assert sandboxes(test_settings.database_path)["Acme"] == "classic"


def test_cancelling_opens_the_subscriptions_own_sandbox(test_settings, monkeypatch):
    import agent.sandbox
    import agent.vision_agent
    gmail = FakeGmail([message("a1", 1, "Acme|9.99|2026-07-01"), message("a2", 2, "Acme|12.99|2026-07-31"),
                       message("k1", 3, "Kinora|149|2026-08-05"), message("k2", 4, "Kinora|199|2026-09-04")])
    notices = {n.merchant_name: n for n in run_pipeline_once.run(client=gmail, extractor=fake_extractor, notify=False)}
    started = []
    monkeypatch.setattr(agent.sandbox, "mock_site", nullcontext)
    monkeypatch.setattr(agent.vision_agent, "run_agent", lambda start_url=None: started.append(urlparse(start_url).path) or "run")
    run_pipeline_once.start_review_agent(notices["Acme"])
    run_pipeline_once.start_review_agent(notices["Kinora"])
    assert started == ["/", "/s/maze/"]


def test_a_named_agent_scenario_overrides_the_rotation(test_settings, monkeypatch):
    import agent.sandbox
    monkeypatch.setattr(agent.sandbox, "settings", dataclasses.replace(test_settings, agent_scenario="trick"))
    run_pipeline_once.run(client=FakeGmail([message("a1", 1, "Acme|9.99|2026-07-01"), message("a2", 2, "Acme|12.99|2026-07-31")]),
                          extractor=fake_extractor, notify=False)
    assert agent.sandbox.sandbox_for_subscription(Database(test_settings.database_path), 1) == "trick"
