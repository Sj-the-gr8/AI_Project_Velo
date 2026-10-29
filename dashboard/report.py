"""Report calculations for the dashboard: pure pandas over the SQLite tables, no Streamlit, so they can be tested.

Amounts in different currencies are never added together; totals are kept per currency.
"""
from __future__ import annotations

import json
import sqlite3
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

MONTHLY_DAYS = 30

RATES_URL = "https://api.frankfurter.dev/v1/latest?base=USD"  # European Central Bank reference rates, no API key
# Units of each currency per 1 USD: ECB reference rates of 23 Oct 2026 (AED is pegged to the dollar).
# Used when live rates cannot be fetched, so the charts still work offline.
FALLBACK_PER_USD = {"USD": 1.0, "INR": 94.94, "EUR": 0.89342, "GBP": 0.74804, "CAD": 1.3995, "AUD": 1.424,
                    "JPY": 156.8, "SGD": 1.2785, "AED": 3.6725}
FALLBACK_DATE = "2026-10-23"


@dataclass(frozen=True)
class Rates:
    per_usd: dict[str, float]
    as_of: str
    live: bool

    def convert(self, amount: float, currency: str, target: str) -> float | None:
        source, goal = self.per_usd.get(str(currency).upper()), self.per_usd.get(target.upper())
        return None if source is None or goal is None else amount / source * goal


def fetch_rates(timeout: float = 4.0) -> Rates:
    """Today's ECB rates, or the built-in ones when the service cannot be reached. Only currency codes are sent."""
    try:
        request = urllib.request.Request(RATES_URL, headers={"User-Agent": "Mozilla/5.0 (Velo dashboard)"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read())
        live = {code.upper(): float(value) for code, value in data["rates"].items()}
        return Rates({**FALLBACK_PER_USD, **live, "USD": 1.0}, str(data["date"]), True)
    except (OSError, ValueError, KeyError, TypeError):
        return Rates(dict(FALLBACK_PER_USD), FALLBACK_DATE, False)


def in_currency(frame: pd.DataFrame, rates: Rates, target: str, amount: str = "amount", currency: str = "currency") -> pd.Series:
    """The `amount` column converted to `target`; NaN where a currency has no rate."""
    if frame.empty:
        return pd.Series(dtype=float, index=frame.index)
    return frame.apply(lambda row: rates.convert(row[amount], row[currency], target), axis=1).astype(float).round(2)


@dataclass
class Tables:
    subscriptions: pd.DataFrame
    events: pd.DataFrame
    alerts: pd.DataFrame
    runs: pd.DataFrame
    actions: pd.DataFrame


def load(database_path: str | Path) -> Tables:
    with sqlite3.connect(database_path) as connection:
        read = lambda query: pd.read_sql_query(query, connection)  # noqa: E731
        return Tables(
            subscriptions=read("SELECT * FROM subscriptions ORDER BY merchant_name"),
            events=read("SELECT b.*, s.merchant_name FROM billing_events b JOIN subscriptions s ON s.id = b.subscription_id "
                        "ORDER BY b.billing_date"),
            alerts=read("SELECT a.*, s.merchant_name, s.currency FROM alerts a JOIN subscriptions s ON s.id = a.subscription_id "
                        "ORDER BY a.created_at DESC"),
            runs=read("SELECT r.*, s.merchant_name FROM agent_runs r LEFT JOIN subscriptions s ON s.id = r.subscription_id "
                      "ORDER BY r.started_at DESC"),
            actions=read("SELECT * FROM agent_actions ORDER BY run_id, step_number, id"),
        )


def money(amount: float, currency: str) -> str:
    symbol = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹"}.get(str(currency).upper())
    return f"{symbol}{amount:,.2f}" if symbol else f"{amount:,.2f} {currency}"


def totals(amounts: pd.Series, currencies: pd.Series) -> str:
    """'$43.98 + ₹194.00' style total, one figure per currency; '—' when empty."""
    if amounts.empty:
        return "—"
    grouped = pd.DataFrame({"amount": amounts.values, "currency": currencies.values}).groupby("currency")["amount"].sum()
    return " + ".join(money(value, currency) for currency, value in grouped.items()) or "—"


def subscription_report(tables: Tables, today: date | None = None) -> pd.DataFrame:
    """One row per subscription with its price history, totals, cadence, next charge, alerts, and agent outcome."""
    today = today or date.today()
    columns = ["id", "merchant", "status", "currency", "current_price", "first_price", "price_change_pct", "charges",
               "total_paid", "first_charge", "last_charge", "avg_days_between", "next_charge", "next_charge_note",
               "monthly_cost", "yearly_cost", "open_alerts", "sandbox", "cancelled_by_agent"]
    if tables.subscriptions.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    for subscription in tables.subscriptions.itertuples():
        events = tables.events[tables.events["subscription_id"] == subscription.id].sort_values("billing_date")
        dates = pd.to_datetime(events["billing_date"])
        first_price = float(events["amount"].iloc[0]) if not events.empty else subscription.current_amount
        gaps = dates.diff().dt.days.dropna()
        last_charge = dates.iloc[-1].date() if not events.empty else None
        next_charge, next_charge_note = None, ""
        if subscription.status == "active" and last_charge:
            # The date the latest receipt states, else a month after it. Never projected past a missing receipt:
            # if that date has gone by with no new receipt, it is reported as late rather than replaced by a guess.
            stated = events["next_billing_date"].iloc[-1] if "next_billing_date" in events else None
            next_charge = date.fromisoformat(stated) if isinstance(stated, str) and stated else last_charge + timedelta(days=MONTHLY_DAYS)
            days = (next_charge - today).days
            next_charge_note = ("today" if days == 0 else f"in {days} days" if days > 0
                                else f"{-days} days late, no receipt yet")
        alerts = tables.alerts[(tables.alerts["subscription_id"] == subscription.id) & (tables.alerts["resolved"] == 0)]
        runs = tables.runs[(tables.runs["subscription_id"] == subscription.id) & (tables.runs["outcome"] == "success")]
        rows.append({
            "id": subscription.id,
            "merchant": subscription.merchant_name,
            "status": subscription.status,
            "currency": subscription.currency,
            "current_price": subscription.current_amount,
            "first_price": first_price,
            "price_change_pct": round((subscription.current_amount - first_price) / first_price * 100, 1) if first_price else 0.0,
            "charges": len(events),
            "total_paid": round(float(events["amount"].sum()), 2),
            "first_charge": dates.iloc[0].date() if not events.empty else None,
            "last_charge": last_charge,
            "avg_days_between": round(float(gaps.mean()), 1) if not gaps.empty else None,
            "next_charge": next_charge,
            "next_charge_note": next_charge_note,
            "monthly_cost": subscription.current_amount if subscription.status == "active" else 0.0,
            "yearly_cost": round(subscription.current_amount * 12, 2) if subscription.status == "active" else 0.0,
            "open_alerts": ", ".join(reason.replace("_", " ") for reason in alerts["reason"]) or "",
            "sandbox": getattr(subscription, "sandbox", None) or "",
            "cancelled_by_agent": runs["finished_at"].iloc[0][:10] if not runs.empty else "",
        })
    return pd.DataFrame(rows, columns=columns)


def summary(tables: Tables, report: pd.DataFrame) -> dict[str, str | int]:
    active = report[report["status"] == "active"]
    cancelled = report[report["status"] == "cancelled"]
    priced_up = report[report["price_change_pct"] > 0]
    return {
        "monthly_spend": totals(active["monthly_cost"], active["currency"]),
        "yearly_spend": totals(active["yearly_cost"], active["currency"]),
        "active": len(active),
        "cancelled": len(cancelled),
        "open_alerts": int((tables.alerts["resolved"] == 0).sum()) if not tables.alerts.empty else 0,
        "total_paid": totals(tables.events["amount"], tables.events["currency"]),
        "charges": len(tables.events),
        "saved_monthly": totals(cancelled["current_price"], cancelled["currency"]),
        "saved_yearly": totals(cancelled["current_price"] * 12, cancelled["currency"]),
        "price_increases": len(priced_up),
        "agent_runs": len(tables.runs),
        "agent_successes": int((tables.runs["outcome"] == "success").sum()) if not tables.runs.empty else 0,
    }


def alert_state(alert) -> str:
    """open, kept (the user chose to keep the subscription), or cancelled (the agent cancelled it)."""
    if not alert.resolved:
        return "open"
    resolution = getattr(alert, "resolution", None)
    return resolution if isinstance(resolution, str) and resolution else "resolved"


def upcoming_charges(report: pd.DataFrame, days: int = 30, today: date | None = None) -> pd.DataFrame:
    """Charges expected from today to `days` ahead. Dates that have already passed are never shown here."""
    today = today or date.today()
    due = report.dropna(subset=["next_charge"])
    due = due[(due["next_charge"] >= today) & (due["next_charge"] <= today + timedelta(days=days))]
    return due[["merchant", "next_charge", "next_charge_note", "current_price", "currency"]].sort_values("next_charge")


def late_charges(report: pd.DataFrame, today: date | None = None) -> pd.DataFrame:
    """Active subscriptions whose expected charge date has passed with no new receipt (missed, failed, or cancelled)."""
    today = today or date.today()
    due = report.dropna(subset=["next_charge"])
    due = due[due["next_charge"] < today]
    return due[["merchant", "next_charge", "next_charge_note", "last_charge", "current_price", "currency"]].sort_values("next_charge")


def monthly_totals(tables: Tables) -> pd.DataFrame:
    """Total charged per calendar month and merchant (what actually left the account that month)."""
    if tables.events.empty:
        return pd.DataFrame(columns=["month", "merchant_name", "currency", "amount"])
    # "2026-07" labels: one bar per month (a date axis would draw each bar as a hairline), and they sort correctly.
    events = tables.events.assign(month=pd.to_datetime(tables.events["billing_date"]).dt.strftime("%Y-%m"))
    return events.groupby(["month", "merchant_name", "currency"], as_index=False)["amount"].sum()


def run_report(tables: Tables) -> pd.DataFrame:
    if tables.runs.empty:
        return pd.DataFrame(columns=["run_id", "started", "scenario", "merchant", "outcome", "steps", "wrong_turns",
                                     "backtracks", "model_calls", "seconds"])
    runs = tables.runs.copy()
    started, finished = pd.to_datetime(runs["started_at"]), pd.to_datetime(runs["finished_at"])
    return pd.DataFrame({
        "run_id": runs["run_id"],
        "started": started.dt.strftime("%Y-%m-%d %H:%M"),
        "scenario": runs["scenario"],
        "merchant": runs["merchant_name"].fillna("(evaluation run)"),
        "outcome": runs["outcome"],
        "steps": runs["steps"],
        "wrong_turns": runs["wrong_turns"],
        "backtracks": runs["backtracks"],
        "model_calls": runs["model_calls"],
        "seconds": (finished - started).dt.total_seconds().round(1),
    })


def markdown_report(tables: Tables, report: pd.DataFrame, generated: str) -> str:
    """A self-contained written report of everything Velo knows, for download."""
    facts = summary(tables, report)
    lines = [f"# Velo subscription report", f"Generated {generated}.", "", "## Summary", "",
             f"- Monthly recurring spend: {facts['monthly_spend']} ({facts['yearly_spend']} a year)",
             f"- Active subscriptions: {facts['active']}; cancelled: {facts['cancelled']}",
             f"- Total paid across {facts['charges']} recorded charges: {facts['total_paid']}",
             f"- Saved by cancelling: {facts['saved_monthly']} a month ({facts['saved_yearly']} a year)",
             f"- Price increases detected: {facts['price_increases']}; open alerts: {facts['open_alerts']}",
             f"- Cancellation agent: {facts['agent_successes']} of {facts['agent_runs']} runs succeeded", "",
             "## Subscriptions", "", "| Merchant | Status | Price | Change | Charges | Total paid | Last charge | Next charge | Alerts |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in report.itertuples():
        lines.append(f"| {row.merchant} | {row.status} | {money(row.current_price, row.currency)} | {row.price_change_pct:+.1f}% | "
                     f"{row.charges} | {money(row.total_paid, row.currency)} | {row.last_charge or '—'} | {f'{row.next_charge} ({row.next_charge_note})' if row.next_charge else '—'} | "
                     f"{row.open_alerts or '—'} |")
    lines += ["", "## Alerts", ""]
    if tables.alerts.empty:
        lines.append("No alerts.")
    for alert in tables.alerts.itertuples():
        state = alert_state(alert)
        lines.append(f"- {alert.created_at[:10]} · {alert.merchant_name}: {alert.reason.replace('_', ' ')} ({state})")
    runs = run_report(tables)
    lines += ["", "## Cancellation agent runs", ""]
    if runs.empty:
        lines.append("No agent runs.")
    for run in runs.itertuples():
        lines.append(f"- {run.started} · {run.scenario} · {run.merchant}: {run.outcome} in {run.steps} steps "
                     f"({run.wrong_turns} wrong turns, {run.backtracks} backtracks, {run.model_calls} Gemini calls)")
    return "\n".join(lines) + "\n"
