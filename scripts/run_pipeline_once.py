from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __package__ in (None, ""):
    # Allow `python scripts/run_pipeline_once.py` from the project root.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from db.db import Database
from extraction.extractor import extract_event
from extraction.schema import BillingEvent
from ingestion.gmail_client import GmailClient
from notify.notifier import AlertNotice, describe_alert, notify_alerts
from rules.trigger_engine import BillingRecord, evaluate_subscription

logger = logging.getLogger(__name__)


def start_review_agent(notice: AlertNotice) -> str | None:
    """Start the sandbox agent only after the user presses "Cancel subscription" (on a notification or the dashboard).

    Returns the run's outcome, or None if another cancellation is already running.
    """
    from agent.run_lock import AgentBusy, agent_lock
    from agent.sandbox import mock_site, sandbox_for_subscription, scenario_url
    from agent.vision_agent import run_agent

    database = Database(settings.database_path)
    with database.connection() as connection:
        subscription = connection.execute("SELECT current_amount, currency FROM subscriptions WHERE id = ?",
                                          (int(notice.alert.subscription_id),)).fetchone()
    # The sandbox shows the subscription being cancelled (its name, price, and currency), not a default brand.
    sandbox = sandbox_for_subscription(database, int(notice.alert.subscription_id))
    start_url = scenario_url(sandbox, merchant=notice.merchant_name, price=subscription["current_amount"] if subscription else None,
                             currency=subscription["currency"] if subscription else None)
    try:
        with agent_lock(settings.database_path, notice.merchant_name), mock_site():
            run_id = run_agent(start_url=start_url)
    except AgentBusy as busy:
        print(f"Not starting a cancellation for {notice.merchant_name}: {busy}.")
        return None
    database.link_run_to_subscription(run_id, int(notice.alert.subscription_id))
    outcome = database.agent_run_outcome(run_id)
    if outcome == "success":
        # Only a confirmed cancellation changes the record; a stopped run leaves it active with the alert open.
        database.mark_cancelled(int(notice.alert.subscription_id))
        print(f"Cancelled {notice.merchant_name} (agent run {run_id}). Marked cancelled and its alerts resolved.")
    else:
        print(f"Agent run {run_id} for {notice.merchant_name} ended without a cancellation confirmation ({outcome}); "
              "the subscription stays active. See agent_actions.")
    return outcome


def _history(connection, subscription_id: int) -> list[BillingRecord]:
    rows = connection.execute("SELECT * FROM billing_events WHERE subscription_id = ? ORDER BY billing_date", (subscription_id,)).fetchall()
    return [BillingRecord(str(subscription_id), row["amount"], datetime.fromisoformat(row["billing_date"]), row["raw_snippet"]) for row in rows]


def notice_for_alert(database: Database, alert_id: int) -> AlertNotice | None:
    """Rebuild the notification for a stored alert, or None if it is no longer open or its subscription is not active."""
    from rules.trigger_engine import Alert, AlertReason

    with database.connection() as connection:
        row = connection.execute(
            "SELECT a.subscription_id, a.reason, a.resolved, s.merchant_name, s.currency, s.status "
            "FROM alerts a JOIN subscriptions s ON s.id = a.subscription_id WHERE a.id = ?", (alert_id,)).fetchone()
        if row is None or row["resolved"] or row["status"] != "active":
            return None
        history = _history(connection, row["subscription_id"])
    alert = Alert(AlertReason(row["reason"]), str(row["subscription_id"]))
    return AlertNotice(row["merchant_name"], alert, describe_alert(alert, history, row["currency"]) if history else "", alert_id)


def keep_alert(notice: AlertNotice) -> None:
    """Do nothing on a notification: the user saw the alert and is keeping the subscription, so the alert closes."""
    if notice.alert_id is not None:
        Database(settings.database_path).keep_alert(notice.alert_id)
        print(f"Keeping {notice.merchant_name}; the {notice.alert.reason.value.replace('_', ' ')} alert is closed.")


def _store_event(database: Database, message: dict, event: BillingEvent) -> int:
    billing_date = event.billing_date.isoformat()
    with database.connection() as connection:
        # Messages can arrive out of order, so only the newest charge sets the current amount.
        connection.execute(
            """INSERT INTO subscriptions (merchant_name, canonical_id, first_seen, last_seen, current_amount, currency)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(canonical_id) DO UPDATE SET
                   first_seen = MIN(first_seen, excluded.first_seen),
                   current_amount = CASE WHEN excluded.last_seen >= last_seen THEN excluded.current_amount ELSE current_amount END,
                   currency = CASE WHEN excluded.last_seen >= last_seen THEN excluded.currency ELSE currency END,
                   last_seen = MAX(last_seen, excluded.last_seen)""",
            (event.merchant.name, event.merchant.canonical_id, billing_date, billing_date, float(event.amount), event.currency),
        )
        subscription = connection.execute("SELECT id FROM subscriptions WHERE canonical_id = ?", (event.merchant.canonical_id,)).fetchone()
        connection.execute(
            "INSERT OR IGNORE INTO billing_events (subscription_id, amount, currency, message_id, received_date, billing_date, next_billing_date, raw_snippet) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (subscription["id"], float(event.amount), event.currency, message["message_id"], message["received_date"], billing_date,
             event.next_billing_date_guess.isoformat() if event.next_billing_date_guess else None, event.raw_text[:2000]),
        )
    return subscription["id"]


def _evaluate_and_record(database: Database, subscription_id: int) -> AlertNotice | None:
    """Run the rules over the full history and save a new alert unless the same one is already open."""
    with database.connection() as connection:
        subscription = connection.execute("SELECT merchant_name, currency, current_amount FROM subscriptions WHERE id = ?",
                                          (subscription_id,)).fetchone()
        history = _history(connection, subscription_id)
        alert = evaluate_subscription(history)
        if alert is None:
            return None
        # Skip an alert that is already open, or one the user chose to keep at this same price. A new price
        # (for example a second hike) raises a fresh alert.
        existing_alert = connection.execute(
            "SELECT 1 FROM alerts WHERE subscription_id = ? AND reason = ? "
            "AND (resolved = 0 OR (resolution = 'kept' AND price = ?))",
            (subscription_id, alert.reason.value, subscription["current_amount"]),
        ).fetchone()
        if existing_alert is not None:
            return None
        alert_id = connection.execute(
            "INSERT INTO alerts (subscription_id, reason, created_at, price) VALUES (?, ?, ?, ?)",
            # Microseconds keep two alerts for the same reason in the same second (one kept, one new) distinct.
            (subscription_id, alert.reason.value, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f"),
             subscription["current_amount"]),
        ).lastrowid
    # Give the subscription its cancellation sandbox now, so the dashboard shows it before anyone presses Cancel.
    from agent.sandbox import sandbox_for_subscription
    sandbox_for_subscription(database, subscription_id)
    return AlertNotice(subscription["merchant_name"], alert, describe_alert(alert, history, subscription["currency"]), alert_id)


def run(client: GmailClient | None = None, extractor=extract_event, notify: bool = True, quiet: bool = False) -> list[AlertNotice]:
    """Process new mail once. ``quiet`` suppresses the summary when there was no new mail (used by the watcher)."""
    database = Database(settings.database_path)
    database.migrate()
    client = client or GmailClient(settings.gmail_credentials_file,
                                   settings.gmail_token_file,
                                   settings.gmail_user,
                                   settings.gmail_cursor_file)
    since = None if settings.gmail_cursor_file.exists() else datetime.now(timezone.utc) - timedelta(days=settings.gmail_lookback_days)
    messages = client.fetch_billing_emails(since=since)

    affected: set[int] = set()
    stored = skipped = 0
    for message in messages:
        with database.connection() as connection:
            seen = connection.execute("SELECT 1 FROM billing_events WHERE message_id = ?", (message["message_id"],)).fetchone()
        if seen is None:
            event = extractor(message)
            if event is None:
                skipped += 1
            else:
                affected.add(_store_event(database, message, event))
                stored += 1
        # Advance only after the message is handled, so a crash re-fetches it next run.
        client.save_cursor(message["internal_date"])

    new_alerts = [result for subscription_id in sorted(affected) if (result := _evaluate_and_record(database, subscription_id))]
    if messages or not quiet:
        print(f"{datetime.now():%H:%M:%S} Fetched {len(messages)} messages: {stored} billing events stored, {skipped} skipped, {len(new_alerts)} new alerts.")

    # Alerts are committed before any notification, so the agent can write to the database.
    if notify and new_alerts:
        for notice in new_alerts:
            print(f"  {notice.merchant_name}: {notice.details}")
        print(f"Waiting up to {settings.notify_click_timeout:.0f}s for a decision on the notifications...")
        if not notify_alerts(new_alerts, start_review_agent, timeout=settings.notify_click_timeout, on_keep=keep_alert):
            print("Cancel subscription was not chosen; the agent was not started.")
    return new_alerts


if __name__ == "__main__":
    # Currency symbols must not crash a legacy-codepage Windows console.
    sys.stdout.reconfigure(errors="replace")
    logging.basicConfig(level=logging.INFO)
    run()
