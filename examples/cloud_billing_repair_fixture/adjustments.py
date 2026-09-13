"""Coupon and service-credit calculations."""


def percentage_coupon(subtotal_cents: int, coupon_percent: int) -> int:
    """Return the half-up coupon discount for a subtotal."""

    if subtotal_cents < 0:
        raise ValueError("subtotal cannot be negative")
    if not 0 <= coupon_percent <= 100:
        raise ValueError("coupon percent must be between 0 and 100")
    return (subtotal_cents * coupon_percent) // 100


def applied_service_credit(remaining_cents: int, credit_cents: int) -> int:
    """Apply at most the amount still owed after a coupon."""

    if remaining_cents < 0:
        raise ValueError("remaining charge cannot be negative")
    if credit_cents < 0:
        raise ValueError("service credit cannot be negative")
    return credit_cents
