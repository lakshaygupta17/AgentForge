class CheckoutService:
    def calculate_total(self, prices):
        return sum(prices)


def validate_coupon(code):
    return code.startswith("SAVE")
