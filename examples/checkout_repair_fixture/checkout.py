"""Checkout total orchestration for the repair fixture."""

from collections.abc import Iterable
from dataclasses import dataclass

from discounts import discounted_line_total
from models import LineItem
from taxes import tax_for_item


@dataclass(frozen=True, slots=True)
class CheckoutTotals:
    subtotal_cents: int
    discount_cents: int
    shipping_cents: int
    tax_cents: int
    total_cents: int


def calculate_checkout(
    items: Iterable[LineItem],
    *,
    discount_percent: int = 0,
    tax_basis_points: int = 825,
    standard_shipping_cents: int = 500,
    free_shipping_threshold_cents: int = 5_000,
) -> CheckoutTotals:
    """Calculate a complete checkout using integer cents."""

    basket = tuple(items)
    if standard_shipping_cents < 0:
        raise ValueError("shipping cannot be negative")
    if free_shipping_threshold_cents < 0:
        raise ValueError("free-shipping threshold cannot be negative")
    if not basket:
        return CheckoutTotals(0, 0, 0, 0, 0)

    subtotal = sum(item.line_total_cents for item in basket)
    discounted_lines = tuple(
        discounted_line_total(item, discount_percent) for item in basket
    )
    discounted_subtotal = sum(discounted_lines)
    discount = subtotal - discounted_subtotal
    shipping = (
        0
        if subtotal >= free_shipping_threshold_cents
        else standard_shipping_cents
    )
    tax = sum(
        tax_for_item(item, discounted, tax_basis_points)
        for item, discounted in zip(basket, discounted_lines, strict=True)
    )
    return CheckoutTotals(
        subtotal,
        discount,
        shipping,
        tax,
        discounted_subtotal + shipping + tax,
    )
