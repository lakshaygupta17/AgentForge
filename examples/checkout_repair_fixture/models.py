"""Small domain objects for the checkout repair fixture."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LineItem:
    sku: str
    unit_price_cents: int
    quantity: int = 1
    taxable: bool = True

    def __post_init__(self) -> None:
        if not self.sku.strip():
            raise ValueError("sku must not be empty")
        if self.unit_price_cents < 0:
            raise ValueError("unit price cannot be negative")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")

    @property
    def line_total_cents(self) -> int:
        return self.unit_price_cents * self.quantity
