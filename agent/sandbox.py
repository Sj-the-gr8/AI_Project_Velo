from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from urllib.parse import urlencode
from pathlib import Path

from config import settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# Order in which subscriptions get a sandbox when AGENT_SCENARIO is "auto": the first one to raise an alert gets the
# classic flow, the next ones each get a different dark pattern, then the rotation repeats.
ROTATION = ("classic", "maze", "trick", "pause_trap", "buried")


def sandbox_for_subscription(database, subscription_id: int) -> str:
    """AGENT_SCENARIO when it names a sandbox; with "auto", the one this subscription was given (assigned on first use)."""
    if settings.agent_scenario != "auto":
        return settings.agent_scenario
    return database.sandbox_for(subscription_id, ROTATION)


def scenario_url(name: str | None = None, merchant: str | None = None, price: float | None = None,
                 currency: str | None = None) -> str:
    """Start URL for a sandbox scenario: "classic" is the original flow at /, the others live at /s/<name>/.

    Given a subscription's merchant, price, and currency, the sandbox shows that subscription instead of its default brand.
    """
    name = name or settings.agent_scenario
    if name == "auto":  # no subscription to look up (for example a manual run): use the original flow
        name = "classic"
    base = settings.mock_site_url.rstrip("/")
    url = f"{base}/" if name == "classic" else f"{base}/s/{name}/"
    account = {key: value for key, value in (("merchant", merchant), ("price", price), ("currency", currency)) if value is not None}
    return f"{url}?{urlencode(account)}" if account else url


def mock_site_running(url: str | None = None) -> bool:
    try:
        with urllib.request.urlopen(url or settings.mock_site_url, timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


@contextmanager
def mock_site(url: str | None = None, startup_timeout: float = 15):
    """Use the local mock site, starting it for the duration of the block if it is not already up."""
    url = url or settings.mock_site_url
    if mock_site_running(url):
        yield
        return
    process = subprocess.Popen([sys.executable, "-m", "agent.mock_site.app"], cwd=PROJECT_ROOT)
    try:
        deadline = time.monotonic() + startup_timeout
        while not mock_site_running(url):
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"Mock site did not start at {url}")
            time.sleep(0.2)
        yield
    finally:
        process.terminate()
        process.wait(timeout=10)
