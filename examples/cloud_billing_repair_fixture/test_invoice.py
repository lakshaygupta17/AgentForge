"""Acceptance contract for the cloud-billing repair challenge."""

from unittest import TestCase

from adjustments import applied_service_credit, percentage_coupon
from invoice import calculate_invoice
from models import BillingInput, InvoiceTotals
from pricing import graduated_usage_charge, prorated_base_fee


class CloudBillingAcceptanceTests(TestCase):
    def test_full_month_base_fee_is_unchanged(self) -> None:
        self.assertEqual(prorated_base_fee(10_000, 30, 30), 10_000)

    def test_proration_rounds_an_exact_half_cent_up(self) -> None:
        self.assertEqual(prorated_base_fee(101, 15, 30), 51)

    def test_proration_handles_an_odd_length_period(self) -> None:
        self.assertEqual(prorated_base_fee(100, 1, 3), 33)

    def test_included_usage_has_no_charge(self) -> None:
        self.assertEqual(graduated_usage_charge(100), 0)

    def test_standard_usage_band_costs_two_cents_per_unit(self) -> None:
        self.assertEqual(graduated_usage_charge(600), 1_000)

    def test_excess_usage_keeps_the_complete_standard_band(self) -> None:
        self.assertEqual(graduated_usage_charge(1_200), 2_000)

    def test_coupon_discount_rounds_half_up(self) -> None:
        self.assertEqual(percentage_coupon(105, 10), 11)

    def test_service_credit_is_capped_at_the_remaining_charge(self) -> None:
        self.assertEqual(applied_service_credit(400, 900), 400)

    def test_basic_invoice_combines_base_usage_and_tax(self) -> None:
        result = calculate_invoice(BillingInput(3_000, 30, 30, 600, tax_basis_points=825))
        self.assertEqual(result, InvoiceTotals(3_000, 1_000, 4_000, 0, 0, 4_000, 330, 4_330))

    def test_combined_adjustments_change_the_tax_base(self) -> None:
        result = calculate_invoice(
            BillingInput(
                10_000,
                15,
                30,
                1_200,
                coupon_percent=15,
                service_credit_cents=950,
                tax_basis_points=725,
            )
        )
        self.assertEqual(result, InvoiceTotals(5_000, 2_000, 7_000, 1_050, 950, 5_000, 363, 5_363))

    def test_tax_exempt_account_pays_no_tax(self) -> None:
        result = calculate_invoice(
            BillingInput(4_000, 30, 30, 600, tax_basis_points=825, tax_exempt=True)
        )
        self.assertEqual(result.tax_cents, 0)
        self.assertEqual(result.total_cents, 5_000)

    def test_oversized_credit_can_reduce_the_invoice_to_zero(self) -> None:
        result = calculate_invoice(
            BillingInput(1_000, 30, 30, 0, service_credit_cents=2_000, tax_basis_points=825)
        )
        self.assertEqual(result, InvoiceTotals(1_000, 0, 1_000, 0, 1_000, 0, 0, 0))

    def test_invalid_business_inputs_are_rejected(self) -> None:
        invalid_inputs = (
            {"monthly_base_cents": -1, "active_days": 1, "billing_days": 30, "usage_units": 0},
            {"monthly_base_cents": 1, "active_days": -1, "billing_days": 30, "usage_units": 0},
            {"monthly_base_cents": 1, "active_days": 31, "billing_days": 30, "usage_units": 0},
            {"monthly_base_cents": 1, "active_days": 0, "billing_days": 0, "usage_units": 0},
            {"monthly_base_cents": 1, "active_days": 1, "billing_days": 30, "usage_units": -1},
            {
                "monthly_base_cents": 1,
                "active_days": 1,
                "billing_days": 30,
                "usage_units": 0,
                "coupon_percent": 101,
            },
            {
                "monthly_base_cents": 1,
                "active_days": 1,
                "billing_days": 30,
                "usage_units": 0,
                "service_credit_cents": -1,
            },
            {
                "monthly_base_cents": 1,
                "active_days": 1,
                "billing_days": 30,
                "usage_units": 0,
                "tax_basis_points": 10_001,
            },
        )
        for values in invalid_inputs:
            with self.subTest(values=values), self.assertRaises(ValueError):
                BillingInput(**values)
