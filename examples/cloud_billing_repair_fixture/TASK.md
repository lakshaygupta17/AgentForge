# Repair task: cloud usage invoices

A small SaaS provider is producing incorrect monthly invoices when partial-month
subscriptions, graduated API usage, coupons, service credits, and tax interact.

Fix the implementation so the complete acceptance suite passes. Preserve the
existing public functions and data classes. Do not modify the tests.

Business rules:

1. All money is represented as integer cents; floating-point arithmetic is forbidden.
2. The monthly base fee is prorated by active days and rounded to the nearest cent,
   with an exact half cent rounded upward.
3. The first 100 API units are included. The next 900 units cost 2 cents each,
   and units above 1,000 cost 1 cent each.
4. A percentage coupon applies to the combined prorated base fee and usage charge.
   Its discount is rounded to the nearest cent, with half cents rounded upward.
5. A fixed service credit applies after the coupon and cannot exceed the remaining
   charge.
6. Tax is calculated on the amount remaining after both adjustments, rounded half
   upward. Tax-exempt accounts pay no tax.
7. Empty charges remain zero, and every invalid negative value, invalid percentage,
   invalid basis-point rate, or impossible day count raises `ValueError`.

Run the acceptance suite from this directory with:

```powershell
python -m unittest discover -v
```
