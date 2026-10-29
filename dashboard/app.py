from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

# `streamlit run dashboard/app.py` only puts dashboard/ on the path; add the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent import run_lock
from config import settings
from dashboard import report as rp
from db.db import Database
from notify.notifier import describe_alert
from rules.trigger_engine import Alert, AlertReason, BillingRecord

st.set_page_config(page_title="Velo", page_icon="V", layout="wide")
st.title("Velo subscription review")

# Create empty tables on a fresh install so the page renders before the first pipeline run.
Database(settings.database_path).migrate()

REFRESH_SECONDS = 30


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def exchange_rates() -> rp.Rates:
    """ECB rates, fetched at most every 6 hours (they change once a working day)."""
    return rp.fetch_rates()
MONEY = st.column_config.NumberColumn(format="%.2f")
DATE = st.column_config.DateColumn(format="D MMM YYYY")


def alert_details(tables: rp.Tables, alert) -> str:
    """The same explanation the notification showed, rebuilt from the stored charges."""
    events = tables.events[tables.events["subscription_id"] == alert.subscription_id]
    history = [BillingRecord(str(alert.subscription_id), row.amount, datetime.fromisoformat(row.billing_date), row.raw_snippet)
               for row in events.itertuples()]
    try:
        return describe_alert(Alert(AlertReason(alert.reason), str(alert.subscription_id)), history, alert.currency) if history else ""
    except (IndexError, ValueError):
        return ""


def md(text: str) -> str:
    """Escape $ so Streamlit does not render the text between two amounts as a LaTeX formula."""
    return text.replace("$", "\\$")


def start_cancellation(alert_id: int, merchant: str) -> None:
    """Run scripts/cancel_subscription.py in the background: the same agent run as the notification's button.

    It runs as its own process so the dashboard stays responsive while the agent's browser window works.
    """
    database = Path(settings.database_path).resolve()
    log = open(database.with_suffix(".agent.log"), "a", encoding="utf-8")
    subprocess.Popen([sys.executable, str(PROJECT_ROOT / "scripts" / "cancel_subscription.py"), "--alert-id", str(alert_id)],
                     cwd=PROJECT_ROOT, stdout=log, stderr=subprocess.STDOUT,
                     env={**os.environ, "DATABASE_PATH": str(database), "PYTHONIOENCODING": "utf-8"})
    log.close()  # the child process keeps its own handle
    st.session_state["launched"] = {"merchant": merchant, "at": time.time()}


def cancellation_in_progress() -> dict | None:
    """The running cancellation, including one just launched whose process has not taken the lock yet."""
    running = run_lock.current(settings.database_path)
    launched = st.session_state.get("launched")
    if running is None and launched and time.time() - launched["at"] < 30:
        return {"merchant": launched["merchant"], "started": datetime.fromtimestamp(launched["at"]).isoformat()}
    return running


def screenshot(path: str) -> Path | None:
    if not path:
        return None
    candidate = Path(path) if Path(path).is_absolute() else PROJECT_ROOT / path
    return candidate if candidate.exists() else None


# Re-read the database on a timer so mail processed by scripts/watch_inbox.py shows up without a manual refresh.
@st.fragment(run_every=REFRESH_SECONDS)
def render() -> None:
    tables = rp.load(settings.database_path)
    report = rp.subscription_report(tables)
    facts = rp.summary(tables, report)
    rates = exchange_rates()
    target = settings.display_currency.upper() if settings.display_currency.upper() in rates.per_usd else "USD"

    row = st.columns(4)
    row[0].metric("Monthly recurring spend", facts["monthly_spend"])
    row[1].metric("Projected yearly spend", facts["yearly_spend"])
    row[2].metric("Active subscriptions", facts["active"])
    row[3].metric("Open alerts", facts["open_alerts"])
    row = st.columns(4)
    row[0].metric("Total paid to date", facts["total_paid"], help=f"Across {facts['charges']} recorded charges")
    row[1].metric("Saved by cancelling (yearly)", facts["saved_yearly"], help=f"{facts['saved_monthly']} a month, from cancelled subscriptions")
    row[2].metric("Cancelled subscriptions", facts["cancelled"])
    row[3].metric("Price increases detected", facts["price_increases"])

    running = cancellation_in_progress()
    if running:
        st.info(f"Cancelling **{running['merchant']}**: the agent is working in its own browser window. "
                "This page updates when it finishes.", icon="⏳")

    overview, subscriptions, alerts, history, agent, written = st.tabs(
        ["Overview", "Subscriptions", "Alerts", "Billing history", "Cancellation agent", "Report"])

    with overview:
        if report.empty:
            st.info("No subscriptions have been ingested yet. Run scripts/watch_inbox.py or scripts/run_pipeline_once.py.")
        else:
            left, right = st.columns(2)
            with left:
                st.subheader("Upcoming charges (next 30 days)")
                upcoming = rp.upcoming_charges(report)
                if upcoming.empty:
                    st.success("No charges expected in the next 30 days.")
                else:
                    st.dataframe(upcoming, hide_index=True, use_container_width=True,
                                 column_config={"next_charge": DATE, "next_charge_note": "when", "current_price": MONEY})
                late = rp.late_charges(report)
                if not late.empty:
                    st.subheader("Expected charges with no receipt yet")
                    st.caption("The latest receipt named a next billing date that has passed, and no newer receipt has arrived. "
                               "The payment may have failed, or the subscription may have ended.")
                    st.dataframe(late, hide_index=True, use_container_width=True,
                                 column_config={"next_charge": st.column_config.DateColumn("expected", format="D MMM YYYY"),
                                                "next_charge_note": "status", "last_charge": DATE, "current_price": MONEY})
            with right:
                st.subheader("Monthly cost by subscription")
                active = report[report["status"] == "active"]
                if active.empty:
                    st.info("No active subscriptions.")
                else:
                    st.bar_chart(active.assign(converted=rp.in_currency(active, rates, target, "monthly_cost")),
                                 x="merchant", y="converted", x_label="", y_label=f"Monthly cost ({target})", horizontal=True)
            # Charts share one axis, so every amount is converted to the display currency first; tables keep originals.
            st.subheader("Total charged per month")
            monthly = rp.monthly_totals(tables)
            st.bar_chart(monthly.assign(converted=rp.in_currency(monthly, rates, target)), x="month", y="converted",
                         color="merchant_name", x_label="Month", y_label=f"Charged that month ({target})")
            st.subheader("Price per charge")
            # One dot per charge, one line per merchant: a price hike reads as a step up, and a subscription with a
            # single charge (which a plain line chart cannot draw) still shows as a dot.
            charges = tables.events.assign(billing_date=pd.to_datetime(tables.events["billing_date"]),
                                           converted=rp.in_currency(tables.events, rates, target))
            st.altair_chart(alt.Chart(charges).mark_line(point=True).encode(
                x=alt.X("billing_date:T", title="Charge date"),
                y=alt.Y("converted:Q", title=f"Amount charged ({target})"),
                color=alt.Color("merchant_name:N", title="merchant"),
                tooltip=[alt.Tooltip("merchant_name:N", title="merchant"), alt.Tooltip("billing_date:T", title="date"),
                         alt.Tooltip("amount:Q", title="charged", format=",.2f"), alt.Tooltip("currency:N"),
                         alt.Tooltip("converted:Q", title=f"in {target}", format=",.2f")]), use_container_width=True)
            source = "live European Central Bank reference rates" if rates.live else "built-in European Central Bank rates (live rates unavailable)"
            # Quote the rate the readable way round (1 USD = ₹94.94, not 1 INR = $0.01).
            per_unit = ", ".join(f"1 {target} = {rp.money(rates.convert(1, target, code), code)}"
                                 for code in sorted(set(tables.events["currency"]) - {target}) if rates.convert(1, target, code))
            st.caption(f"Charts show every amount in {target}, converted at {source} of {rates.as_of}"
                       + (f" ({per_unit})" if per_unit else "") + ". Tables keep each charge in its own currency.")

    with subscriptions:
        st.subheader("All subscriptions")
        if report.empty:
            st.info("No subscriptions yet.")
        else:
            st.dataframe(report.drop(columns=["id"]), hide_index=True, use_container_width=True, column_config={
                "current_price": MONEY, "first_price": MONEY, "total_paid": MONEY, "monthly_cost": MONEY, "yearly_cost": MONEY,
                "price_change_pct": st.column_config.NumberColumn("price change", format="%+.1f%%"),
                "first_charge": DATE, "last_charge": DATE, "next_charge": DATE, "next_charge_note": "next charge status",
                "avg_days_between": st.column_config.NumberColumn("avg days between charges")})
            choice = st.selectbox("Details for", report["merchant"], key="subscription_detail")
            chosen = report[report["merchant"] == choice].iloc[0]
            charges = tables.events[tables.events["subscription_id"] == chosen["id"]]
            st.markdown(md(f"**{choice}** · {chosen['status']} · {rp.money(chosen['current_price'], chosen['currency'])} a month · "
                           f"{chosen['charges']} charges totalling {rp.money(chosen['total_paid'], chosen['currency'])}"))
            st.dataframe(charges[["billing_date", "amount", "currency", "received_date", "message_id"]], hide_index=True,
                         use_container_width=True, column_config={"amount": MONEY})
            for event in charges.itertuples():
                with st.expander(f"Email for the {event.billing_date} charge"):
                    st.text(event.raw_snippet)

    with alerts:
        st.subheader("Open alerts")
        open_alerts = tables.alerts[tables.alerts["resolved"] == 0]
        if open_alerts.empty:
            st.success("No open alerts.")
        for alert in open_alerts.itertuples():
            details, action = st.columns([5, 1], vertical_alignment="center")
            details.warning(md(f"**{alert.merchant_name}: {alert.reason.replace('_', ' ')}** (raised {alert.created_at[:16]})\n\n"
                               f"{alert_details(tables, alert)}"))
            with action:
                if running:
                    st.button("Cancel subscription", key=f"cancel-{alert.id}", disabled=True, use_container_width=True,
                              help="Another cancellation is running")
                else:
                    # A confirm step, so a stray click never starts a cancellation: the human decides, as on the notification.
                    with st.popover("Cancel subscription", use_container_width=True):
                        assigned = report.loc[report["id"] == alert.subscription_id, "sandbox"]
                        sandbox = (settings.agent_scenario if settings.agent_scenario != "auto"
                                   else (assigned.iloc[0] if not assigned.empty and assigned.iloc[0] else "next sandbox in turn"))
                        st.markdown(md(f"Run the cancellation agent for **{alert.merchant_name}**? A browser window opens on the "
                                       f"`{sandbox}` sandbox and the subscription is marked cancelled only if the agent confirms it."))
                        if st.button("Yes, cancel it", key=f"confirm-{alert.id}", type="primary"):
                            start_cancellation(int(alert.id), alert.merchant_name)
                            st.rerun()
                # The dashboard's Do nothing: the alert closes as kept and is not raised again at this price.
                if st.button("Keep subscription", key=f"keep-{alert.id}", use_container_width=True,
                             help="Close this alert. A further price change raises a new one."):
                    Database(settings.database_path).keep_alert(int(alert.id))
                    st.rerun()
        st.subheader("Alert history")
        if tables.alerts.empty:
            st.info("No alerts have been raised yet.")
        else:
            st.dataframe(tables.alerts.assign(state=tables.alerts.apply(rp.alert_state, axis=1))
                         [["created_at", "merchant_name", "reason", "state"]], hide_index=True, use_container_width=True)

    with history:
        st.subheader("Every recorded charge")
        if tables.events.empty:
            st.info("No charges recorded yet.")
        else:
            st.dataframe(tables.events[["billing_date", "merchant_name", "amount", "currency", "received_date", "message_id"]]
                         .sort_values("billing_date", ascending=False), hide_index=True, use_container_width=True,
                         column_config={"amount": MONEY})
            st.caption(f"Extraction mode: {settings.extraction_mode}. Each row comes from one Gmail message; the message id is unique, "
                       "so re-reading mail never double-counts.")

    with agent:
        st.subheader("Cancellation agent runs")
        runs = rp.run_report(tables)
        if runs.empty:
            st.info("The agent has not run yet. Press Cancel subscription on an alert, or run scripts/evaluate_agent.py.")
        else:
            left, middle, right = st.columns(3)
            left.metric("Runs", len(runs))
            middle.metric("Succeeded", int((runs["outcome"] == "success").sum()))
            right.metric("Wrong turns recovered", int(runs["wrong_turns"].sum()))
            st.dataframe(runs.drop(columns=["run_id"]), hide_index=True, use_container_width=True)
            labels = {f"{run.started} · {run.scenario} · {run.outcome}": run.run_id for run in runs.itertuples()}
            run_id = labels[st.selectbox("Step-by-step trace for", list(labels), key="run_detail")]
            steps = tables.actions[tables.actions["run_id"] == run_id]
            st.dataframe(steps[["step_number", "action", "chosen_element_label", "reasoning_text", "timestamp"]],
                         hide_index=True, use_container_width=True)
            shots = [(step.step_number, step.action, path) for step in steps.itertuples() if (path := screenshot(step.screenshot_path))]
            if shots:
                st.caption("What the agent saw at each step (the numbered badges are the Set-of-Mark labels):")
                for start in range(0, len(shots), 3):
                    for column, (number, action, path) in zip(st.columns(3), shots[start:start + 3]):
                        column.image(str(path), caption=f"Step {number}: {action}")

    with written:
        generated = datetime.now().strftime("%d %b %Y %H:%M")
        text = rp.markdown_report(tables, report, generated)
        st.markdown(md(text))
        left, middle, right = st.columns(3)
        left.download_button("Download report (Markdown)", text, file_name="velo-report.md", mime="text/markdown")
        middle.download_button("Subscriptions (CSV)", report.to_csv(index=False), file_name="velo-subscriptions.csv", mime="text/csv")
        right.download_button("All charges (CSV)", tables.events.drop(columns=["raw_snippet"]).to_csv(index=False),
                              file_name="velo-charges.csv", mime="text/csv")

    st.caption(f"Updated {datetime.now():%H:%M:%S}; refreshes every {REFRESH_SECONDS}s. Database: {settings.database_path}")


render()
