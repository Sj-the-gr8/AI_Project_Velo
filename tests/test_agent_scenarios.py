"""The agent against each dark-pattern sandbox, driven by a stand-in model that deliberately takes wrong turns.

The stand-in reads the labelled options from the prompt and follows a script of intents. Each script makes the
mistakes a careless model would (claiming a discount, pausing instead of cancelling, pressing a 'Cancel' button
that keeps the membership, wandering into help pages), so the tests check that the agent notices, backs out,
never repeats a wrong turn, and still finishes the cancellation within the step budget.
"""
import json
import re
import sqlite3
from dataclasses import replace

import pytest

import agent.vision_agent as vision_agent
from agent.vision_agent import run_agent


class IntentClient:
    """Follows ("click", text) / ("go_back",) / ("scroll_down",) intents. A click whose target is not offered
    scrolls instead (and keeps the intent), so scroll position never makes the script brittle."""

    def __init__(self, intents):
        self.intents = list(intents)
        self.prompts = []

    def generate_content(self, parts):
        prompt = parts[0]
        self.prompts.append(prompt)
        offered = dict((text, int(label)) for label, text in re.findall(r'(\d+) "([^"]*)"', prompt.split("Do NOT")[0]))
        intent = self.intents[0]
        if intent[0] == "click":
            label = next((label for text, label in offered.items() if text.startswith(intent[1])), None)
            if label is None:
                return self._reply({"action": "scroll_down", "reasoning": f"looking for {intent[1]}"})
            self.intents.pop(0)
            return self._reply({"action": "click", "label": label, "reasoning": f"click {intent[1]}"})
        self.intents.pop(0)
        return self._reply({"action": intent[0], "reasoning": intent[0]})

    @staticmethod
    def _reply(payload):
        response = type("Response", (), {})()
        response.text = json.dumps(payload)
        return response


def run_scenario(test_settings, mock_site_url, monkeypatch, name, intents):
    monkeypatch.setattr(vision_agent, "settings", replace(test_settings, mock_site_url=mock_site_url, agent_max_steps=15))
    client = IntentClient(intents)
    run_id = run_agent(start_url=f"{mock_site_url}/s/{name}/", client=client, headless=True)
    with sqlite3.connect(test_settings.database_path) as connection:
        actions = [row[0] for row in connection.execute(
            "SELECT action FROM agent_actions WHERE run_id = ? ORDER BY step_number", (run_id,))]
        run = connection.execute("SELECT scenario, outcome, wrong_turns, backtracks, model_calls FROM agent_runs WHERE run_id = ?",
                                 (run_id,)).fetchone()
    return actions, run, client


def test_trick_recovers_from_discount_trap_and_misleading_cancel_button(test_settings, mock_site_url, monkeypatch):
    actions, run, client = run_scenario(test_settings, mock_site_url, monkeypatch, "trick", [
        ("click", "Cancel membership"),
        ("click", "Claim 50% off"),            # wrong: a discount, not a cancellation
        ("go_back",),
        ("click", "No thanks"),
        ("click", "Cancel"),                   # wrong: this 'Cancel' keeps the membership and loops to the start
        ("click", "Cancel membership"),
        ("click", "No thanks"),
        ("click", "Yes, end membership"),
    ])
    assert actions[-1] == "success" and "go_back" in actions
    assert run == ("trick", "success", 2, 1, 8)
    # On the second visit, both wrong options are withheld from the model.
    assert "Do NOT choose labels [1]" in client.prompts[6] and "Do NOT choose labels [1]" in client.prompts[7]


def test_maze_backs_out_of_dead_ends_and_no_op_buttons(test_settings, mock_site_url, monkeypatch):
    actions, run, _ = run_scenario(test_settings, mock_site_url, monkeypatch, "maze", [
        ("click", "Help centre"),
        ("click", "Contact support"),          # dead end: a page with nothing to click, left automatically
        ("click", "Settings"),
        ("click", "Profile"),
        ("click", "Save changes"),             # does nothing: marked as a wrong turn, then the page is left
        ("click", "Billing"),
        ("click", "Manage membership"),
        ("click", "End membership"),
        ("click", "Confirm end of membership"),
    ])
    assert actions.count("auto_back") == 3 and actions[-1] == "success"
    assert run[1] == "success" and run[3] == 3 and run[4] == 9  # 3 backtracks, 9 model calls


def test_pause_trap_backs_out_of_pause_and_answers_survey(test_settings, mock_site_url, monkeypatch):
    actions, run, client = run_scenario(test_settings, mock_site_url, monkeypatch, "pause_trap", [
        ("click", "Cancel or change plan"),
        ("click", "Pause for 3 months"),       # wrong: pausing is not cancelling
        ("go_back",),
        ("click", "I still want to cancel"),
        ("click", "Too expensive"),            # enables the Continue button
        ("click", "Continue"),
        ("click", "Finish cancellation"),
    ])
    assert actions[-1] == "success" and run[1:4] == ("success", 1, 1)
    assert "Continue" not in client.prompts[4] and '"Continue"' in client.prompts[5]  # disabled until a reason is chosen


def test_buried_scrolls_to_footer_and_abandons_catalog(test_settings, mock_site_url, monkeypatch):
    actions, run, _ = run_scenario(test_settings, mock_site_url, monkeypatch, "buried", [
        ("click", "Explore more shows"),
        ("click", "Play trailer"),             # does nothing; the catalog is then a dead end
        ("click", "Cancel membership"),        # in the footer: reached by scrolling
        ("click", "Confirm cancellation"),
    ])
    assert actions[-1] == "success" and "auto_back" in actions
    assert any(action in ("scroll_down", "auto_scroll") for action in actions)
    assert run[1] == "success"


def test_repeating_a_known_wrong_turn_is_refused(test_settings, mock_site_url, monkeypatch):
    class Stubborn(IntentClient):
        """Insists on the label it already knows is wrong, ignoring the instruction not to."""
        def generate_content(self, parts):
            if "Do NOT choose labels [1]" in parts[0] and self.intents and self.intents[0] == ("stubborn",):
                self.intents.pop(0)
                self.prompts.append(parts[0])
                return self._reply({"action": "click", "label": 1, "reasoning": "try the discount again"})
            return super().generate_content(parts)

    monkeypatch.setattr(vision_agent, "settings", replace(test_settings, mock_site_url=mock_site_url, agent_max_steps=15))
    client = Stubborn([("click", "Cancel membership"), ("click", "Claim 50% off"), ("go_back",), ("stubborn",),
                       ("click", "No thanks"), ("click", "Yes, end membership")])
    run_id = run_agent(start_url=f"{mock_site_url}/s/trick/", client=client, headless=True)
    with sqlite3.connect(test_settings.database_path) as connection:
        actions = [row[0] for row in connection.execute("SELECT action FROM agent_actions WHERE run_id = ? ORDER BY step_number", (run_id,))]
    assert "repeat_wrong_turn" in actions and actions[-1] == "success"


@pytest.mark.parametrize("name", ["maze", "trick", "pause_trap", "buried"])
def test_every_scenario_page_is_reachable_and_has_one_success_page(mock_site_url, name):
    import urllib.request
    from agent.mock_site.app import SCENARIOS

    successes = []
    for page in SCENARIOS[name]["pages"]:
        with urllib.request.urlopen(f"{mock_site_url}/s/{name}/{page}") as response:
            assert response.status == 200
            if vision_agent.SUCCESS_TEXT in response.read().decode().lower():
                successes.append(page)
    assert successes == ["done"]
