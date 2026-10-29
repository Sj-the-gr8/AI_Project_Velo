from db.db import Database

class ActionLogger:
    def __init__(self, database: Database, run_id: str):
        self.database = database
        self.run_id = run_id

    def log(self, step_number: int, screenshot_path: str, label: str | None, reasoning: str, action: str | None = None) -> None:
        self.database.log_agent_action(self.run_id, step_number, screenshot_path, label, reasoning, action)
