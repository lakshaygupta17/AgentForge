"""Public immutable models for the fulfillment settlement challenge."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class OrderLine:
    sku: str
    quantity: int
    unit_price_cents: int
    unit_weight_grams: int
    taxable: bool = True

    def __post_init__(self) -> None:
        if not self.sku.strip():
            raise ValueError("sku cannot be empty")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.unit_price_cents < 0:
            raise ValueError("unit price cannot be negative")
        if self.unit_weight_grams < 0:
            raise ValueError("unit weight cannot be negative")


@dataclass(frozen=True, slots=True)
class StockBatch:
    sku: str
    warehouse_id: str
    batch_id: str
    quantity: int
    expires_on: date

    def __post_init__(self) -> None:
        if not self.sku.strip() or not self.warehouse_id.strip() or not self.batch_id.strip():
            raise ValueError("stock identifiers cannot be empty")
        if self.quantity < 0:
            raise ValueError("stock quantity cannot be negative")


@dataclass(frozen=True, slots=True)
class Parcel:
    length_cm: int
    width_cm: int
    height_cm: int

    def __post_init__(self) -> None:
        if self.length_cm <= 0 or self.width_cm <= 0 or self.height_cm <= 0:
            raise ValueError("parcel dimensions must be positive")


@dataclass(frozen=True, slots=True)
class OrderRequest:
    lines: tuple[OrderLine, ...]
    order_date: date
    shipping_zone: str
    parcel: Parcel
    promotion_percent: int = 0
    promotion_cap_cents: int | None = None
    expedited: bool = False
    store_credit_cents: int = 0
    tax_basis_points: int = 0
    tax_shipping: bool = False
    tax_exempt: bool = False

    def __post_init__(self) -> None:
        if not self.lines:
            raise ValueError("order must contain at least one line")
        if len({line.sku for line in self.lines}) != len(self.lines):
            raise ValueError("duplicate skus are not allowed")
        if self.shipping_zone not in {"domestic", "export"}:
            raise ValueError("unknown shipping zone")
        if not 0 <= self.promotion_percent <= 100:
            raise ValueError("promotion percent must be between 0 and 100")
        if self.promotion_cap_cents is not None and self.promotion_cap_cents < 0:
            raise ValueError("promotion cap cannot be negative")
        if self.store_credit_cents < 0:
            raise ValueError("store credit cannot be negative")
        if not 0 <= self.tax_basis_points <= 10_000:
            raise ValueError("tax basis points must be between 0 and 10000")


@dataclass(frozen=True, slots=True)
class Allocation:
    sku: str
    warehouse_id: str
    batch_id: str
    quantity: int


@dataclass(frozen=True, slots=True)
class Settlement:
    merchandise_cents: int
    promotion_cents: int
    promotion_allocations: tuple[int, ...]
    shipping_cents: int
    taxable_cents: int
    tax_cents: int
    store_credit_cents: int
    total_cents: int
    inventory_allocations: tuple[Allocation, ...]
