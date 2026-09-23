"""Promotion calculation and proportional allocation."""


def percentage_promotion(subtotal_cents: int, percent: int, cap_cents: int | None) -> int:
    if subtotal_cents < 0:
        raise ValueError("subtotal cannot be negative")
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")
    if cap_cents is not None and cap_cents < 0:
        raise ValueError("cap cannot be negative")
    discount = subtotal_cents * percent // 100
    return discount if cap_cents is None else min(discount, cap_cents)


def allocate_proportionally(line_subtotals: tuple[int, ...], amount_cents: int) -> tuple[int, ...]:
    if any(value < 0 for value in line_subtotals) or amount_cents < 0:
        raise ValueError("allocation inputs cannot be negative")
    total = sum(line_subtotals)
    if amount_cents > total:
        raise ValueError("allocation cannot exceed subtotal")
    if total == 0:
        return tuple(0 for _ in line_subtotals)
    return tuple(amount_cents * value // total for value in line_subtotals)
