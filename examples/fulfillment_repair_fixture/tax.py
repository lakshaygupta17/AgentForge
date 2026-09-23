"""Aggregate taxable-base and tax helpers."""


def taxable_base(
    line_subtotals: tuple[int, ...],
    promotion_allocations: tuple[int, ...],
    taxable_flags: tuple[bool, ...],
    shipping_cents: int,
    tax_shipping: bool,
) -> int:
    if not (
        len(line_subtotals) == len(promotion_allocations) == len(taxable_flags)
    ):
        raise ValueError("tax inputs must have equal lengths")
    if any(value < 0 for value in line_subtotals + promotion_allocations):
        raise ValueError("tax amounts cannot be negative")
    if shipping_cents < 0:
        raise ValueError("shipping cannot be negative")
    merchandise = sum(
        subtotal - discount
        for subtotal, discount, taxable in zip(
            line_subtotals, promotion_allocations, taxable_flags, strict=True
        )
        if taxable
    )
    return merchandise + shipping_cents


def calculate_tax(taxable_cents: int, basis_points: int, tax_exempt: bool) -> int:
    if taxable_cents < 0 or not 0 <= basis_points <= 10_000:
        raise ValueError("invalid tax inputs")
    return taxable_cents * basis_points // 10_000
