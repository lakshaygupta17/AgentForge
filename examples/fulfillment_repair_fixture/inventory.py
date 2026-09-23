"""Deterministic, all-or-nothing inventory allocation."""

from datetime import date

from models import Allocation, OrderLine, StockBatch


def allocate_inventory(
    lines: tuple[OrderLine, ...], batches: tuple[StockBatch, ...], order_date: date
) -> tuple[Allocation, ...]:
    allocations: list[Allocation] = []
    for line in lines:
        remaining = line.quantity
        candidates = sorted(
            (
                batch
                for batch in batches
                if batch.sku == line.sku and batch.expires_on >= order_date
            ),
            key=lambda batch: (batch.expires_on, batch.batch_id),
        )
        for batch in candidates:
            taken = min(remaining, batch.quantity)
            if taken:
                allocations.append(
                    Allocation(line.sku, batch.warehouse_id, batch.batch_id, taken)
                )
                remaining -= taken
            if remaining == 0:
                break
    return tuple(allocations)
