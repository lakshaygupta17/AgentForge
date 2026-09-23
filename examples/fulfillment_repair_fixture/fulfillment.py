"""Order settlement orchestration."""

from inventory import allocate_inventory
from models import OrderRequest, Settlement, StockBatch
from promotions import allocate_proportionally, percentage_promotion
from shipping import billable_weight_grams, shipping_charge
from tax import calculate_tax, taxable_base


def settle_order(order: OrderRequest, batches: tuple[StockBatch, ...]) -> Settlement:
    line_subtotals = tuple(line.quantity * line.unit_price_cents for line in order.lines)
    merchandise = sum(line_subtotals)
    promotion = percentage_promotion(
        merchandise, order.promotion_percent, order.promotion_cap_cents
    )
    promotion_allocations = allocate_proportionally(line_subtotals, promotion)
    inventory_allocations = allocate_inventory(order.lines, batches, order.order_date)
    actual_weight = sum(line.quantity * line.unit_weight_grams for line in order.lines)
    billed_weight = billable_weight_grams(actual_weight, order.parcel)
    shipping = shipping_charge(
        order.shipping_zone,
        billed_weight,
        merchandise,
        order.expedited,
    )
    taxable = taxable_base(
        line_subtotals,
        promotion_allocations,
        tuple(line.taxable for line in order.lines),
        shipping,
        order.tax_shipping,
    )
    tax = calculate_tax(taxable, order.tax_basis_points, order.tax_exempt)
    credit = order.store_credit_cents
    total = merchandise - promotion + shipping + tax - credit
    return Settlement(
        merchandise,
        promotion,
        promotion_allocations,
        shipping,
        taxable,
        tax,
        credit,
        total,
        inventory_allocations,
    )
