"""Public input and output models for cloud billing."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BillingInput:
    monthly_base_cents: int
    active_days: int
    billing_days: int
    usage_units: int
    coupon_percent: int = 0
    service_credit_cents: int = 0
    tax_basis_points: int = 0
    tax_exempt: bool = False

    def __post_init__(self) -> None:
        if self.monthly_base_cents < 0:
            raise ValueError("monthly base fee cannot be negative")
        if self.billing_days <= 0:
            raise ValueError("billing days must be positive")
        if not 0 <= self.active_days <= self.billing_days:
            raise ValueError("active days must be within the billing period")
        if self.usage_units < 0:
            raise ValueError("usage units cannot be negative")
        if not 0 <= self.coupon_percent <= 100:
            raise ValueError("coupon percent must be between 0 and 100")
        if self.service_credit_cents < 0:
            raise ValueError("service credit cannot be negative")
        if not 0 <= self.tax_basis_points <= 10_000:
            raise ValueError("tax basis points must be between 0 and 10000")


@dataclass(frozen=True, slots=True)
class InvoiceTotals:
    base_cents: int
    usage_cents: int
    subtotal_cents: int
    coupon_cents: int
    credit_cents: int
    taxable_cents: int
    tax_cents: int
    total_cents: int
