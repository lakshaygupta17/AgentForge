"""Acceptance contract for the multi-warehouse fulfillment challenge."""

from datetime import date
from unittest import TestCase

from fulfillment import settle_order
from inventory import allocate_inventory
from models import (
    Allocation,
    OrderLine,
    OrderRequest,
    Parcel,
    Settlement,
    StockBatch,
)
from promotions import allocate_proportionally, percentage_promotion
from shipping import billable_weight_grams, shipping_charge
from tax import calculate_tax, taxable_base

TODAY = date(2026, 9, 13)


def line(sku: str, quantity: int, price: int, weight: int, taxable: bool = True) -> OrderLine:
    return OrderLine(sku, quantity, price, weight, taxable)


def batch(sku: str, warehouse: str, name: str, quantity: int, day: int) -> StockBatch:
    return StockBatch(sku, warehouse, name, quantity, date(2026, 9, day))


class PromotionAcceptanceTests(TestCase):
    def test_percentage_uses_half_up_rounding(self) -> None:
        self.assertEqual(percentage_promotion(105, 10, None), 11)

    def test_zero_cap_really_means_zero_discount(self) -> None:
        self.assertEqual(percentage_promotion(5_000, 20, 0), 0)

    def test_cap_limits_a_rounded_discount(self) -> None:
        self.assertEqual(percentage_promotion(9_999, 15, 1_000), 1_000)

    def test_largest_remainder_allocation_preserves_every_cent(self) -> None:
        self.assertEqual(allocate_proportionally((100, 100, 100), 2), (1, 1, 0))

    def test_largest_remainder_uses_fraction_before_line_size(self) -> None:
        self.assertEqual(allocate_proportionally((50, 30, 20), 7), (4, 2, 1))

    def test_zero_value_lines_can_share_a_zero_promotion(self) -> None:
        self.assertEqual(allocate_proportionally((0, 0), 0), (0, 0))


class InventoryAcceptanceTests(TestCase):
    def test_fefo_excludes_stock_expiring_today(self) -> None:
        batches = (batch("A", "W1", "old", 3, 13), batch("A", "W1", "fresh", 3, 14))
        self.assertEqual(
            allocate_inventory((line("A", 2, 100, 10),), batches, TODAY),
            (Allocation("A", "W1", "fresh", 2),),
        )

    def test_fefo_tie_breaks_by_warehouse_then_batch(self) -> None:
        batches = (
            batch("A", "W2", "a", 1, 20),
            batch("A", "W1", "z", 1, 20),
            batch("A", "W1", "b", 1, 20),
        )
        self.assertEqual(
            allocate_inventory((line("A", 3, 100, 10),), batches, TODAY),
            (
                Allocation("A", "W1", "b", 1),
                Allocation("A", "W1", "z", 1),
                Allocation("A", "W2", "a", 1),
            ),
        )

    def test_allocation_can_split_one_sku_across_batches(self) -> None:
        batches = (batch("A", "W1", "a", 2, 14), batch("A", "W2", "b", 5, 15))
        self.assertEqual(
            allocate_inventory((line("A", 4, 100, 10),), batches, TODAY),
            (Allocation("A", "W1", "a", 2), Allocation("A", "W2", "b", 2)),
        )

    def test_any_sku_shortage_rolls_back_the_entire_order(self) -> None:
        lines = (line("A", 1, 100, 10), line("B", 2, 100, 10))
        batches = (batch("A", "W1", "a", 1, 20), batch("B", "W1", "b", 1, 20))
        self.assertEqual(allocate_inventory(lines, batches, TODAY), ())


class ShippingAcceptanceTests(TestCase):
    def test_dimensional_weight_rounds_up_before_500g_increment(self) -> None:
        parcel = Parcel(31, 20, 20)
        self.assertEqual(billable_weight_grams(400, parcel), 2_500)

    def test_dimensional_weight_keeps_integer_precision_at_large_values(self) -> None:
        parcel = Parcel(2_500_000_000_000_000_001, 1, 1)
        self.assertEqual(
            billable_weight_grams(0, parcel),
            500_000_000_000_000_500,
        )

    def test_actual_weight_also_rounds_up_to_500g(self) -> None:
        self.assertEqual(billable_weight_grams(501, Parcel(10, 10, 10)), 1_000)

    def test_domestic_threshold_is_inclusive_and_post_promotion(self) -> None:
        self.assertEqual(shipping_charge("domestic", 1_000, 10_000, False), 0)
        self.assertEqual(shipping_charge("domestic", 1_000, 9_999, False), 740)

    def test_expedited_shipping_is_not_free_and_uses_a_surcharge(self) -> None:
        self.assertEqual(shipping_charge("domestic", 1_000, 20_000, True), 1_640)

    def test_export_uses_its_own_rate_and_is_never_free(self) -> None:
        self.assertEqual(shipping_charge("export", 1_001, 50_000, False), 1_950)


class TaxAcceptanceTests(TestCase):
    def test_taxable_base_only_includes_shipping_when_configured(self) -> None:
        args = ((1_000, 2_000), (100, 200), (True, False), 700)
        self.assertEqual(taxable_base(*args, False), 900)
        self.assertEqual(taxable_base(*args, True), 1_600)

    def test_tax_rounds_half_up_and_honors_exemption(self) -> None:
        self.assertEqual(calculate_tax(200, 825, False), 17)
        self.assertEqual(calculate_tax(10_000, 825, True), 0)


class SettlementAcceptanceTests(TestCase):
    def test_full_interaction_has_exact_totals_and_deterministic_stock(self) -> None:
        lines = (
            line("MED", 3, 2_500, 300, True),
            line("BOOK", 2, 1_250, 400, False),
            line("KIT", 1, 4_000, 700, True),
        )
        batches = (
            batch("MED", "W2", "m2", 2, 20),
            batch("MED", "W1", "m1", 2, 14),
            batch("BOOK", "W1", "b1", 2, 30),
            batch("KIT", "W3", "k1", 1, 25),
        )
        order = OrderRequest(
            lines,
            TODAY,
            "domestic",
            Parcel(20, 20, 20),
            promotion_percent=15,
            promotion_cap_cents=2_000,
            expedited=True,
            store_credit_cents=20_000,
            tax_basis_points=825,
            tax_shipping=True,
        )
        self.assertEqual(
            settle_order(order, batches),
            Settlement(
                14_000,
                2_000,
                (1_072, 357, 571),
                2_000,
                11_857,
                978,
                14_978,
                0,
                (
                    Allocation("MED", "W1", "m1", 2),
                    Allocation("MED", "W2", "m2", 1),
                    Allocation("BOOK", "W1", "b1", 2),
                    Allocation("KIT", "W3", "k1", 1),
                ),
            ),
        )

    def test_free_shipping_uses_amount_after_promotion(self) -> None:
        lines = (line("A", 1, 11_000, 100),)
        batches = (batch("A", "W", "a", 1, 20),)
        order = OrderRequest(
            lines, TODAY, "domestic", Parcel(10, 10, 10), promotion_percent=10
        )
        self.assertEqual(settle_order(order, batches).shipping_cents, 620)

    def test_store_credit_is_capped_and_does_not_reduce_tax(self) -> None:
        lines = (line("A", 1, 1_000, 100),)
        batches = (batch("A", "W", "a", 1, 20),)
        order = OrderRequest(
            lines,
            TODAY,
            "domestic",
            Parcel(10, 10, 10),
            store_credit_cents=9_999,
            tax_basis_points=1_000,
        )
        result = settle_order(order, batches)
        self.assertEqual(
            (result.tax_cents, result.store_credit_cents, result.total_cents),
            (100, 1_720, 0),
        )

    def test_shortage_prevents_settlement_side_effects(self) -> None:
        lines = (line("A", 2, 1_000, 100),)
        batches = (batch("A", "W", "a", 1, 20),)
        order = OrderRequest(lines, TODAY, "domestic", Parcel(10, 10, 10))
        with self.assertRaises(ValueError):
            settle_order(order, batches)

    def test_invalid_public_inputs_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            OrderLine("A", 0, 100, 1)
        with self.assertRaises(ValueError):
            OrderLine("A", 1, -1, 1)
        with self.assertRaises(ValueError):
            StockBatch("A", "W", "B", -1, TODAY)
        with self.assertRaises(ValueError):
            Parcel(1, 0, 1)
        with self.assertRaises(ValueError):
            OrderRequest((), TODAY, "domestic", Parcel(1, 1, 1))
        duplicated = (line("A", 1, 1, 1), line("A", 2, 1, 1))
        with self.assertRaises(ValueError):
            OrderRequest(duplicated, TODAY, "domestic", Parcel(1, 1, 1))
        with self.assertRaises(ValueError):
            OrderRequest((line("A", 1, 1, 1),), TODAY, "moon", Parcel(1, 1, 1))
        with self.assertRaises(ValueError):
            calculate_tax(100, 10_001, False)
