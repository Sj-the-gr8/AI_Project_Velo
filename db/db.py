from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def migrate(self) -> None:
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        with self.connection() as connection:
            connection.executescript(schema)
            # Databases created before the action column existed.
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(agent_actions)")}
            if "action" not in columns:
                connection.execute("ALTER TABLE agent_actions ADD COLUMN action TEXT")
            # Databases created before receipts' stated next billing date was kept.
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(billing_events)")}
            if "next_billing_date" not in columns:
                connection.execute("ALTER TABLE billing_events ADD COLUMN next_billing_date TEXT")
            # Databases created before each subscription got its own cancellation sandbox.
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(subscriptions)")}
            if "sandbox" not in columns:
                connection.execute("ALTER TABLE subscriptions ADD COLUMN sandbox TEXT")
            # Databases created before alerts recorded how they were closed and the price they were raised at.
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(alerts)")}
            for column, kind in (("resolution", "TEXT"), ("price", "REAL")):
                if column not in columns:
                    connection.execute(f"ALTER TABLE alerts ADD COLUMN {column} {kind}")

    def sandbox_for(self, subscription_id: int, rotation: tuple[str, ...]) -> str:
        """The subscription's cancellation sandbox, assigned on first use as the next one in `rotation`, then kept."""
        with self.connection() as connection:
            row = connection.execute("SELECT sandbox FROM subscriptions WHERE id = ?", (subscription_id,)).fetchone()
            if row and row["sandbox"]:
                return row["sandbox"]
            assigned = connection.execute("SELECT COUNT(*) FROM subscriptions WHERE sandbox IS NOT NULL").fetchone()[0]
            name = rotation[assigned % len(rotation)]
            connection.execute("UPDATE subscriptions SET sandbox = ? WHERE id = ?", (name, subscription_id))
            return name

    def agent_run_outcome(self, run_id: str) -> str | None:
        """Return the action of a run's final row ("success" or "stopped"), or None if it never finished."""
        with self.connection() as connection:
            row = connection.execute(
                "SELECT action FROM agent_actions WHERE run_id = ? ORDER BY step_number DESC, id DESC LIMIT 1", (run_id,)
            ).fetchone()
        return row["action"] if row and row["action"] in ("success", "stopped") else None

    def mark_cancelled(self, subscription_id: int) -> None:
        """Record a confirmed cancellation: the subscription stops counting as active and its alerts close."""
        with self.connection() as connection:
            connection.execute("UPDATE subscriptions SET status = 'cancelled' WHERE id = ?", (subscription_id,))
            connection.execute("UPDATE alerts SET resolved = 1, resolution = 'cancelled' WHERE subscription_id = ? AND resolved = 0",
                               (subscription_id,))

    def keep_alert(self, alert_id: int) -> None:
        """The user looked at the alert and chose to keep the subscription (Do nothing / Keep subscription)."""
        with self.connection() as connection:
            connection.execute("UPDATE alerts SET resolved = 1, resolution = 'kept' WHERE id = ? AND resolved = 0", (alert_id,))

    def record_agent_run(self, run_id: str, scenario: str, started_at: str, outcome: str, steps: int,
                         wrong_turns: int, backtracks: int, model_calls: int) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO agent_runs (run_id, scenario, started_at, finished_at, outcome, steps, wrong_turns, backtracks, model_calls) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, scenario, started_at, datetime.now(timezone.utc).isoformat(), outcome, steps, wrong_turns, backtracks, model_calls),
            )

    def link_run_to_subscription(self, run_id: str, subscription_id: int) -> None:
        with self.connection() as connection:
            connection.execute("UPDATE agent_runs SET subscription_id = ? WHERE run_id = ?", (subscription_id, run_id))

    def log_agent_action(self, run_id: str, step_number: int, screenshot_path: str, label: str | None, reasoning: str, action: str | None = None) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO agent_actions (run_id, step_number, screenshot_path, chosen_element_label, action, reasoning_text, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, step_number, screenshot_path, label, action, reasoning, datetime.now(timezone.utc).isoformat()),
            )
