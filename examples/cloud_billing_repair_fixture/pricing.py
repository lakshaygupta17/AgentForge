"""Base-fee proration and graduated usage pricing."""


def prorated_base_fee(monthly_base_cents: int, active_days: int, billing_days: int) -> int:
    """Return a half-up prorated monthly fee using integer arithmetic."""

    if monthly_base_cents < 0:
        raise ValueError("monthly base fee cannot be negative")
    if billing_days <= 0:
        raise ValueError("billing days must be positive")
    if not 0 <= active_days <= billing_days:
        raise ValueError("active days must be within the billing period")
    numerator = monthly_base_cents * active_days
    return numerator // billing_days


def graduated_usage_charge(usage_units: int) -> int:
    """Price included, standard, and excess API-usage bands."""

    if usage_units < 0:
        raise ValueError("usage units cannot be negative")
    billable_units = max(0, usage_units - 100)
    return billable_units
