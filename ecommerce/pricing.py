"""Price rules shared by the cart, orders and the API."""

TAX_RATE = 0.18
FREE_SHIPPING_FROM = 500


def apply_discount(amount, pct):
    """Take pct percent off amount."""
    return amount - amount * pct / 100


def apply_coupon(amount, code):
    """Apply a coupon code to amount. Unknown codes raise ValueError."""
    if code == "SAVE10":
        return apply_discount(amount, 10)
    if code == "FLAT50":
        return max(amount - 50, 0)
    raise ValueError(f"unknown coupon code: {code!r}")


def tax(amount):
    return round(amount * TAX_RATE, 2)


def shipping(total):
    return 0 if total >= FREE_SHIPPING_FROM else 40


def price_order(subtotal, discount_pct=0, coupon=None):
    """Final price: discount, then coupon, then tax, then shipping."""
    discounted = apply_discount(subtotal, discount_pct)
    if coupon is not None:
        discounted = apply_coupon(discounted, coupon)
    return round(discounted + tax(discounted) + shipping(discounted), 2)
