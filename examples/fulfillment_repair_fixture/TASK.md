# Repair task: multi-warehouse order settlement

An order service produces incorrect totals and inventory reservations when discounts,
multiple stock batches, shipping, store credit, and tax interact.

Repair the implementation so the complete acceptance suite passes. Preserve the public
functions and frozen data classes. Do not modify `test_fulfillment.py`.

Business rules:

1. Money uses integer cents. Every percentage calculation rounds half upward; floats are
   forbidden.
2. A percentage promotion is calculated from the merchandise subtotal, then capped by
   `promotion_cap_cents`. A cap of `None` means no cap.
3. The promotion is allocated across lines in proportion to each line subtotal. Start
   with floor shares, then give leftover cents to the largest fractional remainders.
   Ties follow the original line order. Allocations must sum to the promotion exactly.
4. Inventory is allocated per SKU using FEFO: earliest expiry first, then lexical
   warehouse ID and batch ID. A batch expiring on the order date is unavailable.
   Allocation is all-or-nothing for the whole order: any shortage returns no allocations.
5. A parcel's dimensional weight in grams is `ceil(L*W*H*1000/5000)`. Shipping uses the
   greater of actual and dimensional weight, rounded upward to a 500 g increment.
6. Domestic standard shipping costs 500 cents plus 120 cents per billed 500 g. Export
   standard shipping costs 1200 cents plus 250 cents per billed 500 g. Expedited service
   adds 900 cents after the normal calculation. Domestic standard shipping is free when
   the post-promotion merchandise amount is at least 10,000 cents; export and expedited
   orders never receive free shipping.
7. Tax is calculated once on the sum of taxable post-promotion line amounts plus shipping
   when `tax_shipping` is true. Store credit is a payment, not a discount, so it never
   reduces tax. Tax-exempt orders pay no tax.
8. Store credit is applied last and cannot exceed merchandise after promotion plus
   shipping plus tax. The final total cannot be negative.
9. Duplicate SKUs in an order are invalid. All negative money, quantity, weight, dimension,
   points-like, or tax inputs are invalid; dimensions and quantities must be positive.
   Empty orders and unknown shipping zones are invalid.

Run the acceptance suite from this directory with:

```powershell
python -m unittest discover -v
```
