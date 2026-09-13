"""Sales-tax calculations for checkout merchandise."""

from models import LineItem


def tax_for_item(
    item: LineItem, discounted_total_cents: int, tax_basis_points: int
) -> int:
    """Return rounded tax for one discounted, taxable line."""

    if discounted_total_cents < 0:
        raise ValueError("discounted total cannot be negative")
    if not 0 <= tax_basis_points <= 10_000:
        raise ValueError("tax basis points must be between 0 and 10000")
    return (discounted_total_cents * tax_basis_points + 5_000) // 10_000
