import re
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class Merchant(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    canonical_id: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("merchant name must not be blank")
        return value.strip()

    @field_validator("canonical_id")
    @classmethod
    def normalise_canonical_id(cls, value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        if not slug:
            raise ValueError("canonical_id must contain letters or digits")
        return slug


class BillingEvent(BaseModel):
    merchant: Merchant
    amount: Decimal = Field(gt=Decimal("0"), lt=Decimal("1000000"))
    currency: str = Field(min_length=3, max_length=3)
    billing_date: date
    next_billing_date_guess: date | None = None
    confidence: float = Field(ge=0, le=1)
    trial_converted: bool = False
    raw_text: str = ""

    @field_validator("currency")
    @classmethod
    def uppercase_currency(cls, value: str) -> str:
        return value.upper()
