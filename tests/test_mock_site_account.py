"""The sandbox shows the subscription being cancelled, not a fixed brand."""
from contextlib import nullcontext
from urllib.parse import parse_qs, urlparse

import pytest

from agent.mock_site.app import app
from agent.sandbox import scenario_url


@pytest.fixture
def client():
    return app.test_client()


def test_scenario_shows_the_given_subscription_on_every_page(client):
    start = client.get("/s/trick/?merchant=Kinora&price=199.0&currency=INR").get_data(as_text=True)
    assert "Kinora membership" in start and "₹199.00/month" in start and "FitNow" not in start
    # Later pages carry no query string; the session's cookies keep the subscription.
    discount = client.get("/s/trick/discount").get_data(as_text=True)
    assert "₹99.50/month" in discount
    done = client.get("/s/trick/done").get_data(as_text=True)
    assert "Kinora will not charge you again" in done and "membership is cancelled" in done


def test_classic_flow_shows_the_given_subscription(client):
    page = client.get("/?merchant=PixelPlay&price=10.99&currency=USD").get_data(as_text=True)
    assert "PixelPlay membership" in page and "Next charge: $10.99" in page and "Acme Video" not in page
    assert "only $5.50/month" in client.get("/retention").get_data(as_text=True)


def test_without_details_each_scenario_keeps_its_default_brand(client):
    assert "Acme Video membership" in client.get("/").get_data(as_text=True)
    assert "StreamPlus account" in app.test_client().get("/s/maze/").get_data(as_text=True)


def test_scenario_url_carries_the_subscription(monkeypatch):
    import agent.sandbox
    import dataclasses
    monkeypatch.setattr(agent.sandbox, "settings", dataclasses.replace(agent.sandbox.settings, mock_site_url="http://127.0.0.1:5000"))
    url = scenario_url("trick", merchant="Kinora", price=199.0, currency="INR")
    assert urlparse(url).path == "/s/trick/"
    assert parse_qs(urlparse(url).query) == {"merchant": ["Kinora"], "price": ["199.0"], "currency": ["INR"]}
    assert scenario_url("classic") == "http://127.0.0.1:5000/"


def test_cancelling_passes_that_subscription_to_the_sandbox(test_settings, monkeypatch):
    import agent.sandbox
    import agent.vision_agent
    from db.db import Database
    from scripts import run_pipeline_once
    from tests.test_pipeline import FakeGmail, fake_extractor, message

    gmail = FakeGmail([message("m1", 1, "Kinora|149.00|2026-08-05"), message("m2", 2, "Kinora|199.00|2026-09-04")])
    notice = run_pipeline_once.run(client=gmail, extractor=fake_extractor, notify=False)[0]
    started = []

    def fake_agent(start_url=None):
        started.append(start_url)
        Database(test_settings.database_path).log_agent_action("run-1", 1, "final.png", None, "done", "success")
        return "run-1"

    monkeypatch.setattr(agent.sandbox, "mock_site", nullcontext)
    monkeypatch.setattr(agent.vision_agent, "run_agent", fake_agent)
    run_pipeline_once.start_review_agent(notice)
    query = parse_qs(urlparse(started[0]).query)
    assert query == {"merchant": ["Kinora"], "price": ["199.0"], "currency": ["USD"]}
