# Repair task: checkout totals

Customers are seeing incorrect checkout totals when discounts, tax-exempt items,
and free shipping interact.

Fix the implementation so the complete test suite passes while preserving the
existing public functions and data classes. Do not modify the tests.

Business rules:

1. All monetary values are integer cents; floating-point arithmetic is forbidden.
2. A percentage discount is applied to each line independently and rounded to
   the nearest cent, with half cents rounded upward.
3. Sales tax is calculated only on discounted merchandise marked as taxable.
4. The free-shipping threshold is evaluated against the discounted merchandise
   subtotal, before tax and without including shipping itself.
5. An empty basket has a zero total and no shipping charge.
6. Invalid prices, quantities, percentages, basis points, or shipping values must
   be rejected with `ValueError`.

Run the acceptance suite from this directory with:

```powershell
python -m unittest discover -v
```
