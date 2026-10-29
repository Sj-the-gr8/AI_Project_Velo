"""Send one real desktop notification for a sample alert.

The notification shows the facts behind the alert plus "Cancel subscription" and "Do nothing"
buttons. Pressing either only prints your choice; it does not start the agent, Gmail, or Gemini.

    python scripts/send_test_notification.py [price_hike|dormant|trial_convert] [--merchant NAME] [--timeout SECONDS]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

if __package__ in (None, ""):
    # Allow `python scripts/send_test_notification.py` from the project root.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from notify.notifier import AlertNotice, describe_alert, notify_alerts
from rules.trigger_engine import Alert, AlertReason, BillingRecord

# Sample monthly charge histories that trigger each rule.
SAMPLE_AMOUNTS = {
    AlertReason.PRICE_HIKE: [9.99, 9.99, 9.99, 12.99],
    AlertReason.DORMANT: [10.99] * 6,
    AlertReason.TRIAL_CONVERT: [14.99],
}


def sample_notice(reason: AlertReason, merchant: str) -> AlertNotice:
    amounts = SAMPLE_AMOUNTS[reason]
    start = datetime.now() - timedelta(days=30 * (len(amounts) - 1) + 2)
    history = [BillingRecord("demo", amount, start + timedelta(days=30 * index)) for index, amount in enumerate(amounts)]
    alert = Alert(reason, "demo")
    return AlertNotice(merchant, alert, describe_alert(alert, history))


def main() -> None:
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="Send a sample Velo alert notification.")
    parser.add_argument("reason", nargs="?", default="price_hike", choices=[reason.value for reason in AlertReason])
    parser.add_argument("--merchant", default="Acme Video")
    parser.add_argument("--timeout", type=float, default=120, help="seconds to wait for a button press")
    args = parser.parse_args()

    notice = sample_notice(AlertReason(args.reason), args.merchant)
    print(f"Sent: {notice.merchant_name}: {notice.details}")
    print(f"Waiting {args.timeout:.0f}s for Cancel subscription / Do nothing...")
    print("No popup? Do Not Disturb hides banners; press Win+N and use the buttons in the notification centre while this waits.")
    chosen = notify_alerts([notice], lambda _: None, timeout=args.timeout)
    if chosen:
        print(f"Cancel subscription pressed for {chosen.merchant_name}. The agent was not started (demo).")
    else:
        print("Do nothing pressed, or no answer. Nothing was changed.")


if __name__ == "__main__":
    main()
