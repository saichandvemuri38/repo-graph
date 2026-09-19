from .models import CartItem
from .pricing import price_order


class Cart:
    def __init__(self):
        self.items = []
        self.discount_pct = 0
        self.coupon = None

    def apply_coupon(self, code):
        """Remember a coupon code; total() applies it."""
        self.coupon = code

    def add(self, product, qty=1):
        # Reject zero, negative, fractional or non-numeric quantities: a negative
        # qty would raise stock and produce a negative (refund-like) payment.
        if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
            raise ValueError(f"quantity must be a positive integer, got {qty!r}")
        self.items.append(CartItem(product, qty))

    def subtotal(self):
        return sum(i.subtotal() for i in self.items)

    def total(self):
        return price_order(self.subtotal(), self.discount_pct, self.coupon)
