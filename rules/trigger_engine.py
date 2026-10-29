"""Pure subscription alert rules for Velo."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Sequence


class AlertReason(str, Enum):
    PRICE_HIKE = "price_hike"
    DORMANT = "dormant"
    TRIAL_CONVERT = "trial_convert"


@dataclass(frozen=True)
class BillingRecord:
    subscription_id: str
    amount: float
    billing_date: datetime
    body_text: str = ""
    usage_signal: bool | None = None


@dataclass(frozen=True)
class Alert:
    reason: AlertReason
    subscription_id: str


AMOUNT_TOLERANCE = 0.01
MONTHLY_DAYS = (25, 35)
DORMANT_CYCLES = 6


def _amounts_close(left: float, right: float) -> bool:
    tolerance = max(AMOUNT_TOLERANCE, min(abs(left), abs(right)) * 0.01)
    return abs(left - right) <= tolerance


def _is_monthly(history: Sequence[BillingRecord]) -> bool:
    return all(
        MONTHLY_DAYS[0] <= (current.billing_date - previous.billing_date).days <= MONTHLY_DAYS[1]
        for previous, current in zip(history, history[1:])
    )


def _is_recurring(history: Sequence[BillingRecord]) -> bool:
    if len(history) < 2 or not _is_monthly(history):
        return False
    if len(history) == 2:
        return True
    return all(_amounts_close(previous.amount, current.amount) for previous, current in zip(history[:-2], history[1:-1]))


def evaluate_subscription(history: Sequence[BillingRecord]) -> Alert | None:
    """Return one alert for an ordered subscription history, or ``None``.

    ``history`` may be supplied in any order. Usage signals are optional; when
    absent, cycle count is the conservative dormant proxy required by the demo.
    """
    if not history:
        return None

    ordered = sorted(history, key=lambda record: record.billing_date)
    subscription_id = ordered[-1].subscription_id
    first_text = ordered[0].body_text.lower()
    trial_language = ("trial" in first_text and any(word in first_text for word in ("converted", "ended", "paid", "charge")))
    if len(ordered) == 1 and trial_language:
        return Alert(AlertReason.TRIAL_CONVERT, subscription_id)

    if not _is_recurring(ordered):
        return None

    if len(ordered) >= 2 and ordered[-1].amount > ordered[-2].amount and not _amounts_close(ordered[-1].amount, ordered[-2].amount):
        return Alert(AlertReason.PRICE_HIKE, subscription_id)

    if len(ordered) >= DORMANT_CYCLES and all(record.usage_signal is not True for record in ordered[-DORMANT_CYCLES:]):
        return Alert(AlertReason.DORMANT, subscription_id)

    return None
