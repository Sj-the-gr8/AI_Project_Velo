import sqlite3

from db.db import Database


def test_schema_migration_creates_required_tables(tmp_path):
    path = tmp_path / "velo.db"
    Database(path).migrate()
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"subscriptions", "billing_events", "alerts", "agent_actions"} <= tables


def test_migration_adds_action_column_to_existing_database(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE agent_actions (id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, step_number INTEGER NOT NULL, screenshot_path TEXT NOT NULL, chosen_element_label TEXT, reasoning_text TEXT NOT NULL, timestamp TEXT NOT NULL)")
    database = Database(path)
    database.migrate()
    database.log_agent_action("run", 1, "shot.png", "2", "because", "click")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT action FROM agent_actions").fetchall() == [("click",)]
