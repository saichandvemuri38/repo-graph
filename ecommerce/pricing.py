"""Price rules shared by the cart, orders and the API."""

TAX_RATE = 0.18
FREE_SHIPPING_FROM = 500


def apply_discount(amount, pct):
    """Take pct percent off amount."""
    return amount - amount * pct / 100


def tax(amount):
    return round(amount * TAX_RATE, 2)


def shipping(total):
    return 0 if total >= FREE_SHIPPING_FROM else 40


def price_order(subtotal, discount_pct=0):
    """Final price: discount, then tax, then shipping."""
    discounted = apply_discount(subtotal, discount_pct)
    return round(discounted + tax(discounted) + shipping(discounted), 2)
