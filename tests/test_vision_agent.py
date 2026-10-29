import json
import sqlite3
from dataclasses import replace

import pytest
from pydantic import ValidationError

import agent.vision_agent as vision_agent
from agent.vision_agent import AgentDecision, run_agent


def test_decision_rejects_actions_outside_whitelist():
    with pytest.raises(ValidationError):
        AgentDecision.model_validate({"action": "type", "label": 1, "reasoning": "x"})


def test_agent_refuses_other_origins(test_settings):
    with pytest.raises(ValueError):
        run_agent(start_url="https://example.com/account")


class ScriptedClient:
    """Stands in for Gemini with a fixed sequence of replies."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def generate_content(self, parts):
        self.prompts.append(parts[0])
        response = type("Response", (), {})()
        response.text = self.replies.pop(0)
        return response


def test_agent_completes_mock_cancellation_and_logs_trace(test_settings, mock_site_url, monkeypatch):
    monkeypatch.setattr(vision_agent, "settings", replace(test_settings, mock_site_url=mock_site_url))
    client = ScriptedClient([
        "not json",
        json.dumps({"action": "click", "label": 99, "reasoning": "hallucinated label"}),
        json.dumps({"action": "click", "label": 1, "reasoning": "Small 'Need to cancel?' link"}),
        json.dumps({"action": "dismiss_modal", "label": 2, "reasoning": "Continue past the retention offer"}),
        json.dumps({"action": "click", "label": 1, "reasoning": "Confirm cancellation"}),
    ])
    try:
        run_id = run_agent(client=client, headless=True)
    except Exception as error:
        if "Executable doesn't exist" in str(error):
            pytest.skip("Playwright Chromium is not installed")
        raise

    with sqlite3.connect(test_settings.database_path) as connection:
        trace = connection.execute(
            "SELECT step_number, chosen_element_label, action FROM agent_actions WHERE run_id = ? ORDER BY step_number", (run_id,)
        ).fetchall()
    assert trace == [(1, None, "invalid_response"), (2, "99", "failed_label"), (3, "1", "click"),
                     (4, "2", "dismiss_modal"), (5, "1", "click"), (6, None, "success")]
    # Earlier steps, including failures, are fed back to the model on later steps.
    assert "label not on page" in client.prompts[2]


def test_agent_stops_at_step_cap(test_settings, mock_site_url, monkeypatch):
    monkeypatch.setattr(vision_agent, "settings", replace(test_settings, mock_site_url=mock_site_url, agent_max_steps=3))
    keep = json.dumps({"action": "click", "label": 99, "reasoning": "never valid"})
    run_id = run_agent(client=ScriptedClient([keep] * 3), headless=True)
    with sqlite3.connect(test_settings.database_path) as connection:
        actions = [row[0] for row in connection.execute("SELECT action FROM agent_actions WHERE run_id = ? ORDER BY step_number", (run_id,))]
    assert actions == ["failed_label", "failed_label", "failed_label", "stopped"]
