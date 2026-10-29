from datetime import date

import pandas as pd

from dashboard import report as rp
from db.db import Database

TODAY = date(2026, 9, 24)


def seeded(tmp_path):
    database = Database(tmp_path / "velo.db")
    database.migrate()
    with database.connection() as connection:
        connection.executemany(
            "INSERT INTO subscriptions (id, merchant_name, canonical_id, first_seen, last_seen, current_amount, currency, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(1, "Acme Video", "acme-video", "2026-07-01", "2026-08-30", 12.99, "USD", "active"),
             (2, "NoteNest", "notenest", "2026-09-20", "2026-09-20", 8.00, "USD", "cancelled"),
             (3, "Tunely", "tunely", "2026-09-03", "2026-09-03", 119.0, "INR", "active")])
        connection.executemany(
            "INSERT INTO billing_events (subscription_id, amount, currency, message_id, received_date, billing_date, raw_snippet) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(1, 9.99, "USD", "a1", "x", "2026-07-01", "r"), (1, 9.99, "USD", "a2", "x", "2026-07-31", "r"),
             (1, 12.99, "USD", "a3", "x", "2026-08-30", "r"), (2, 8.00, "USD", "n1", "x", "2026-09-20", "trial ended"),
             (3, 119.0, "INR", "t1", "x", "2026-09-03", "r")])
        connection.executemany("INSERT INTO alerts (subscription_id, reason, created_at, resolved) VALUES (?, ?, ?, ?)",
                               [(1, "price_hike", "2026-09-01 10:00:00", 0), (2, "trial_convert", "2026-09-21 10:00:00", 1)])
    database.record_agent_run("run-1", "trick", "2026-09-21T10:00:00+00:00", "success", 5, 2, 1, 5)
    database.link_run_to_subscription("run-1", 2)
    return rp.load(tmp_path / "velo.db")


def test_subscription_report_has_price_history_totals_and_next_charge(tmp_path):
    report = rp.subscription_report(seeded(tmp_path), today=TODAY).set_index("merchant")
    acme = report.loc["Acme Video"]
    assert (acme.first_price, acme.current_price, acme.price_change_pct, acme.charges, acme.total_paid) == (9.99, 12.99, 30.0, 3, 32.97)
    assert acme.avg_days_between == 30.0 and acme.next_charge == date(2026, 9, 29) and acme.open_alerts == "price hike"
    notenest = report.loc["NoteNest"]
    assert notenest.next_charge is None and notenest.monthly_cost == 0 and notenest.cancelled_by_agent != ""


def test_summary_keeps_currencies_separate_and_counts_savings(tmp_path):
    tables = seeded(tmp_path)
    facts = rp.summary(tables, rp.subscription_report(tables, today=TODAY))
    assert facts["monthly_spend"] == "₹119.00 + $12.99"  # one figure per currency, by currency code
    assert facts["total_paid"] == "₹119.00 + $40.97"
    assert facts["saved_yearly"] == "$96.00"
    assert (facts["active"], facts["cancelled"], facts["open_alerts"], facts["price_increases"]) == (2, 1, 1, 1)
    assert (facts["agent_runs"], facts["agent_successes"]) == (1, 1)


def test_upcoming_charges_and_monthly_totals(tmp_path):
    tables = seeded(tmp_path)
    upcoming = rp.upcoming_charges(rp.subscription_report(tables, today=TODAY), today=TODAY)
    assert list(upcoming["merchant"]) == ["Acme Video", "Tunely"]
    july = rp.monthly_totals(tables)
    assert july[(july["month"] == "2026-07") & (july["merchant_name"] == "Acme Video")]["amount"].item() == 19.98


def test_markdown_report_mentions_everything(tmp_path):
    tables = seeded(tmp_path)
    text = rp.markdown_report(tables, rp.subscription_report(tables, today=TODAY), "today")
    for expected in ("Acme Video", "+30.0%", "price hike (open)", "trial convert (resolved)", "trick", "2 wrong turns"):
        assert expected in text


def test_empty_database_produces_empty_report(tmp_path):
    Database(tmp_path / "empty.db").migrate()
    tables = rp.load(tmp_path / "empty.db")
    report = rp.subscription_report(tables)
    assert report.empty and rp.summary(tables, report)["monthly_spend"] == "—"
    assert rp.run_report(tables).empty and "No alerts." in rp.markdown_report(tables, report, "now")


def test_next_charge_uses_the_stated_date_and_is_never_projected_past_a_missing_receipt(tmp_path):
    database = Database(tmp_path / "velo.db")
    database.migrate()
    with database.connection() as connection:
        connection.execute("INSERT INTO subscriptions (id, merchant_name, canonical_id, first_seen, last_seen, current_amount) "
                           "VALUES (1, 'PageTurn', 'pageturn', '2026-04-20', '2026-04-20', 8.99)")
        connection.execute("INSERT INTO billing_events (subscription_id, amount, currency, message_id, received_date, billing_date, "
                           "next_billing_date, raw_snippet) VALUES (1, 8.99, 'USD', 'p1', 'x', '2026-04-20', '2026-05-20', 'r')")
    report = rp.subscription_report(rp.load(tmp_path / "velo.db"), today=date(2026, 10, 25))
    row = report.iloc[0]
    # The receipt said 20 May; that has passed, so it is reported late, not moved on to a made-up November date.
    assert row.next_charge == date(2026, 5, 20) and row.next_charge_note == "158 days late, no receipt yet"
    assert rp.upcoming_charges(report, today=date(2026, 10, 25)).empty
    assert list(rp.late_charges(report, today=date(2026, 10, 25))["merchant"]) == ["PageTurn"]
    # On a day before that date, it is an upcoming charge instead.
    early = rp.subscription_report(rp.load(tmp_path / "velo.db"), today=date(2026, 5, 1))
    assert early.iloc[0].next_charge_note == "in 19 days"
    assert list(rp.upcoming_charges(early, today=date(2026, 5, 1))["merchant"]) == ["PageTurn"]


def test_pipeline_stores_the_receipts_next_billing_date(test_settings):
    from scripts import run_pipeline_once
    from tests.test_pipeline import FakeGmail, rows
    receipt = {"message_id": "m1", "internal_date": 1, "received_date": "2026-10-25T10:00:00+00:00", "subject": "PageTurn receipt",
               "sender": "me <me@gmail.com>", "body": "Total charged: $8.99\nDate: April 20, 2026\nNext billing date: May 20, 2026"}
    run_pipeline_once.run(client=FakeGmail([receipt]), notify=False)
    assert rows(test_settings.database_path, "SELECT billing_date, next_billing_date FROM billing_events") == [("2026-04-20", "2026-05-20")]


def test_conversion_uses_rates_per_usd():
    rates = rp.Rates({"USD": 1.0, "INR": 95.0, "EUR": 0.9}, "2026-10-23", True)
    assert rates.convert(190, "INR", "USD") == 2.0
    assert round(rates.convert(10, "USD", "INR"), 2) == 950.0
    assert round(rates.convert(9, "EUR", "INR"), 2) == 950.0
    assert rates.convert(5, "XYZ", "USD") is None


def test_in_currency_converts_a_column_and_leaves_unknown_currencies_blank():
    rates = rp.Rates({"USD": 1.0, "INR": 95.0}, "2026-10-23", True)
    frame = pd.DataFrame({"amount": [9.99, 199.0, 5.0], "currency": ["USD", "INR", "XYZ"]})
    converted = rp.in_currency(frame, rates, "USD")
    assert converted.iloc[0] == 9.99 and converted.iloc[1] == 2.09 and pd.isna(converted.iloc[2])


def test_fetch_rates_parses_the_live_response(monkeypatch):
    import io, json, urllib.request

    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *exc): return False

    payload = {"amount": 1.0, "base": "USD", "date": "2026-10-24", "rates": {"INR": 95.5, "EUR": 0.9}}
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: Response(json.dumps(payload).encode()))
    rates = rp.fetch_rates()
    assert rates.live and rates.as_of == "2026-10-24" and rates.per_usd["INR"] == 95.5
    assert rates.per_usd["AED"] == rp.FALLBACK_PER_USD["AED"]  # not in the ECB list, kept from the built-in table


def test_fetch_rates_falls_back_when_offline(monkeypatch):
    import urllib.request

    def offline(request, timeout):
        raise OSError("network unreachable")

    monkeypatch.setattr(urllib.request, "urlopen", offline)
    rates = rp.fetch_rates()
    assert not rates.live and rates.as_of == rp.FALLBACK_DATE and rates.per_usd == rp.FALLBACK_PER_USD
