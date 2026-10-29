from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    gmail_credentials_file: Path = Path(os.getenv("GMAIL_CREDENTIALS_FILE", "client_secret.json"))
    gmail_token_file: Path = Path(os.getenv("GMAIL_TOKEN_FILE", "token.json"))
    gmail_cursor_file: Path = Path(os.getenv("GMAIL_CURSOR_FILE", ".velo_gmail_cursor"))
    gmail_user: str = os.getenv("GMAIL_USER", "me")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    database_path: Path = Path(os.getenv("DATABASE_PATH", "velo.db"))
    gmail_lookback_days: int = int(os.getenv("GMAIL_LOOKBACK_DAYS", "90"))
    mock_site_url: str = os.getenv("MOCK_SITE_URL", "http://127.0.0.1:5000")
    agent_max_steps: int = int(os.getenv("AGENT_MAX_STEPS", "15"))
    agent_screenshot_dir: Path = Path(os.getenv("AGENT_SCREENSHOT_DIR", "agent_screenshots"))
    notify_click_timeout: float = float(os.getenv("NOTIFY_CLICK_TIMEOUT", "120"))
    poll_interval_seconds: float = float(os.getenv("POLL_INTERVAL_SECONDS", "60"))
    extraction_mode: str = os.getenv("EXTRACTION_MODE", "regex")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    agent_scenario: str = os.getenv("AGENT_SCENARIO", "auto")
    display_currency: str = os.getenv("DISPLAY_CURRENCY", "USD")


settings = Settings()
