"""Parcel billable-weight and shipping-price rules."""

from models import Parcel


def billable_weight_grams(actual_weight_grams: int, parcel: Parcel) -> int:
    if actual_weight_grams < 0:
        raise ValueError("actual weight cannot be negative")
    dimensional = parcel.length_cm * parcel.width_cm * parcel.height_cm // 5
    return max(actual_weight_grams, dimensional)


def shipping_charge(
    zone: str,
    billable_grams: int,
    post_promotion_merchandise_cents: int,
    expedited: bool,
) -> int:
    if zone not in {"domestic", "export"}:
        raise ValueError("unknown shipping zone")
    if billable_grams < 0 or post_promotion_merchandise_cents < 0:
        raise ValueError("shipping inputs cannot be negative")
    if zone == "domestic" and post_promotion_merchandise_cents > 10_000:
        return 0
    increments = billable_grams // 500
    base, rate = (500, 120) if zone == "domestic" else (1_200, 250)
    charge = base + increments * rate
    return charge * 2 if expedited else charge
