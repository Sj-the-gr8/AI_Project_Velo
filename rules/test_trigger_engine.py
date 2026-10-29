from datetime import datetime, timedelta

from rules.trigger_engine import AlertReason, BillingRecord, evaluate_subscription


def record(subscription_id="sub-1", amount=100.0, days=0, body="receipt"):
    return BillingRecord(subscription_id, amount, datetime(2026, 1, 1) + timedelta(days=days), body)


def test_price_hike_after_recurring_history():
    history = [record(amount=100, days=0), record(amount=100, days=30), record(amount=120, days=60)]
    assert evaluate_subscription(history) == Alert(AlertReason.PRICE_HIKE, "sub-1")


def test_dormant_after_six_cycles():
    history = [record(amount=100, days=30 * index) for index in range(6)]
    assert evaluate_subscription(history) == Alert(AlertReason.DORMANT, "sub-1")


def test_usage_signal_prevents_dormant_alert():
    history = [record(amount=100, days=30 * index) for index in range(6)]
    history[-1] = BillingRecord(**{**history[-1].__dict__, "usage_signal": True})
    assert evaluate_subscription(history) is None


def test_trial_conversion_is_flagged_without_prior_receipt():
    history = [record(body="Your free trial converted to a paid subscription")]
    assert evaluate_subscription(history) == Alert(AlertReason.TRIAL_CONVERT, "sub-1")


def test_steady_subscription_is_not_flagged():
    history = [record(amount=100, days=30 * index) for index in range(4)]
    assert evaluate_subscription(history) is None


def test_single_receipt_is_not_recurring():
    assert evaluate_subscription([record()]) is None


def test_amount_decrease_is_not_a_price_hike():
    history = [record(amount=100, days=0), record(amount=95, days=30)]
    assert evaluate_subscription(history) is None


def test_rounding_within_tolerance_is_steady():
    history = [record(amount=100.00, days=0), record(amount=100.50, days=30)]
    assert evaluate_subscription(history) is None


def test_non_monthly_history_is_not_recurring():
    history = [record(amount=100, days=0), record(amount=100, days=60)]
    assert evaluate_subscription(history) is None


# Keep the expected value readable without importing implementation internals.
from rules.trigger_engine import Alert


def test_empty_history_is_not_flagged():
    assert evaluate_subscription([]) is None


def test_unordered_history_is_sorted_before_evaluation():
    history = [record(amount=120, days=60), record(amount=100, days=0), record(amount=100, days=30)]
    assert evaluate_subscription(history) == Alert(AlertReason.PRICE_HIKE, "sub-1")


def test_price_hike_takes_precedence_over_dormant():
    history = [record(amount=100, days=30 * index) for index in range(5)] + [record(amount=130, days=150)]
    assert evaluate_subscription(history) == Alert(AlertReason.PRICE_HIKE, "sub-1")


def test_trial_language_on_later_receipts_is_not_trial_convert():
    history = [record(days=0), record(days=30, body="Your free trial converted to a paid plan")]
    assert evaluate_subscription(history) is None


def test_trial_mention_without_conversion_is_not_flagged():
    assert evaluate_subscription([record(body="Start your free trial today")]) is None


def test_price_hike_after_unsteady_history_is_not_flagged():
    history = [record(amount=80, days=0), record(amount=100, days=30), record(amount=120, days=60)]
    assert evaluate_subscription(history) is None
