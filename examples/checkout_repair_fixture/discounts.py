"""Discount calculations for individual checkout lines."""

from models import LineItem


def discounted_line_total(item: LineItem, discount_percent: int) -> int:
    """Return one discounted line total using integer-cent arithmetic."""

    if not 0 <= discount_percent <= 100:
        raise ValueError("discount percent must be between 0 and 100")
    multiplier = 100 - discount_percent
    return (item.line_total_cents * multiplier) // 100
