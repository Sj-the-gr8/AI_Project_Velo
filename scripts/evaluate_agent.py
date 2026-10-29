"""Run the real Gemini agent against each dark-pattern sandbox and report how it did.

    python scripts/evaluate_agent.py                       # every scenario, browser visible
    python scripts/evaluate_agent.py trick maze --headless

Each run uses Gemini once per step (up to AGENT_MAX_STEPS). Runs are recorded in agent_runs and agent_actions,
so they also appear on the dashboard's Agent tab.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

if __package__ in (None, ""):
    # Allow `python scripts/evaluate_agent.py` from the project root.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.mock_site.app import SCENARIOS
from agent.sandbox import mock_site, scenario_url
from agent.vision_agent import run_agent
from config import settings

ALL = ["classic", *SCENARIOS]


def main() -> None:
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="Evaluate the cancellation agent on the sandbox scenarios.")
    parser.add_argument("scenarios", nargs="*", default=ALL, choices=ALL, metavar="SCENARIO", help=f"any of: {', '.join(ALL)}")
    parser.add_argument("--headless", action="store_true", help="do not show the browser")
    args = parser.parse_args()

    results = []
    with mock_site():
        for name in args.scenarios:
            print(f"Running {name}...", flush=True)
            run_id = run_agent(start_url=scenario_url(name), headless=args.headless)
            with sqlite3.connect(settings.database_path) as connection:
                run = connection.execute("SELECT outcome, steps, wrong_turns, backtracks, model_calls FROM agent_runs WHERE run_id = ?",
                                         (run_id,)).fetchone()
                summary = connection.execute("SELECT reasoning_text FROM agent_actions WHERE run_id = ? ORDER BY step_number DESC, id DESC LIMIT 1",
                                             (run_id,)).fetchone()[0]
            results.append((name, *run, run_id))
            print(f"  {run[0]}: {summary}")
            if "quota" in summary.lower():
                print("Gemini quota is exhausted; stopping the evaluation.")
                break

    print(f"\n{'scenario':12} {'outcome':10} {'steps':>5} {'wrong turns':>11} {'backtracks':>10} {'Gemini calls':>12}")
    for name, outcome, steps, wrong, back, calls, _ in results:
        print(f"{name:12} {outcome:10} {steps:>5} {wrong:>11} {back:>10} {calls:>12}")
    passed = sum(1 for result in results if result[1] == "success")
    print(f"\n{passed}/{len(results)} cancelled. Step-by-step traces are in agent_actions and on the dashboard's Agent tab.")


if __name__ == "__main__":
    main()
