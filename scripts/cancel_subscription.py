"""Cancel the subscription behind one open alert, the same way the notification's Cancel subscription button does.

    python scripts/cancel_subscription.py --alert-id 3

The dashboard runs this in the background when you confirm Cancel subscription on an open alert. It starts the mock
site if needed, runs the agent on AGENT_SCENARIO, and marks the subscription cancelled only if the agent succeeds.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):
    # Allow `python scripts/cancel_subscription.py` from the project root.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from db.db import Database
from scripts.run_pipeline_once import notice_for_alert, start_review_agent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the cancellation agent for one open alert.")
    parser.add_argument("--alert-id", type=int, required=True)
    args = parser.parse_args(argv)

    database = Database(settings.database_path)
    database.migrate()
    notice = notice_for_alert(database, args.alert_id)
    if notice is None:
        print(f"Alert {args.alert_id} is not open (or its subscription is already cancelled); nothing to do.")
        return 1
    print(f"Cancelling {notice.merchant_name} ({notice.alert.reason.value}): {notice.details}", flush=True)
    return 0 if start_review_agent(notice) == "success" else 1


if __name__ == "__main__":
    # Currency symbols must not crash a legacy-codepage Windows console.
    sys.stdout.reconfigure(errors="replace")
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
