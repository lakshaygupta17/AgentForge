import unittest

from checkout import CheckoutTotals, calculate_checkout
from discounts import discounted_line_total
from models import LineItem
from taxes import tax_for_item


class CheckoutAcceptanceTests(unittest.TestCase):
    def test_empty_basket_has_no_charges(self) -> None:
        self.assertEqual(calculate_checkout([]), CheckoutTotals(0, 0, 0, 0, 0))

    def test_basic_taxable_checkout_includes_shipping_and_tax(self) -> None:
        result = calculate_checkout([LineItem("book", 1_000, quantity=2)])

        self.assertEqual(result, CheckoutTotals(2_000, 0, 500, 165, 2_665))

    def test_tax_exempt_merchandise_is_not_taxed(self) -> None:
        result = calculate_checkout([LineItem("food", 2_000, taxable=False)])

        self.assertEqual(result.tax_cents, 0)
        self.assertEqual(result.total_cents, 2_500)

    def test_mixed_basket_discounts_each_line_before_tax(self) -> None:
        result = calculate_checkout(
            [
                LineItem("taxable", 1_001),
                LineItem("exempt", 999, taxable=False),
            ],
            discount_percent=10,
        )

        self.assertEqual(result, CheckoutTotals(2_000, 200, 500, 74, 2_374))

    def test_discount_rounds_half_cent_up_per_line(self) -> None:
        item = LineItem("rounding-edge", 105)

        self.assertEqual(discounted_line_total(item, 10), 95)

    def test_discount_can_remove_free_shipping_eligibility(self) -> None:
        result = calculate_checkout(
            [LineItem("promotion", 5_200)], discount_percent=10
        )

        self.assertEqual(result.shipping_cents, 500)
        self.assertEqual(result.total_cents, 5_566)

    def test_discounted_subtotal_can_still_qualify_for_free_shipping(self) -> None:
        result = calculate_checkout(
            [LineItem("large-order", 6_000)], discount_percent=10
        )

        self.assertEqual(result.shipping_cents, 0)
        self.assertEqual(result.total_cents, 5_846)

    def test_invalid_business_inputs_are_rejected(self) -> None:
        with self.subTest("negative price"):
            with self.assertRaises(ValueError):
                LineItem("bad", -1)
        with self.subTest("zero quantity"):
            with self.assertRaises(ValueError):
                LineItem("bad", 100, quantity=0)
        with self.subTest("discount"):
            with self.assertRaises(ValueError):
                discounted_line_total(LineItem("item", 100), 101)
        with self.subTest("tax"):
            with self.assertRaises(ValueError):
                tax_for_item(LineItem("item", 100), 100, -1)
        with self.subTest("shipping"):
            with self.assertRaises(ValueError):
                calculate_checkout(
                    [LineItem("item", 100)], standard_shipping_cents=-1
                )


if __name__ == "__main__":
    unittest.main()
