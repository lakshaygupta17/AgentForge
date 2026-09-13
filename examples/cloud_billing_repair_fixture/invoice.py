"""Invoice orchestration across pricing and adjustment helpers."""

from adjustments import applied_service_credit, percentage_coupon
from models import BillingInput, InvoiceTotals
from pricing import graduated_usage_charge, prorated_base_fee


def calculate_invoice(billing: BillingInput) -> InvoiceTotals:
    """Calculate one complete cloud-usage invoice."""

    base = prorated_base_fee(
        billing.monthly_base_cents,
        billing.active_days,
        billing.billing_days,
    )
    usage = graduated_usage_charge(billing.usage_units)
    subtotal = base + usage
    coupon = percentage_coupon(subtotal, billing.coupon_percent)
    after_coupon = subtotal - coupon
    credit = applied_service_credit(after_coupon, billing.service_credit_cents)
    taxable = after_coupon - credit
    tax = (after_coupon * billing.tax_basis_points + 5_000) // 10_000
    return InvoiceTotals(
        base,
        usage,
        subtotal,
        coupon,
        credit,
        taxable,
        tax,
        taxable + tax,
    )
