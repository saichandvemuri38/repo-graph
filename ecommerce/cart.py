from .models import CartItem
from .pricing import price_order


class Cart:
    def __init__(self):
        self.items = []
        self.discount_pct = 0

    def add(self, product, qty=1):
        self.items.append(CartItem(product, qty))

    def remove(self, sku):
        self.items = [i for i in self.items if i.product.sku != sku]

    def subtotal(self):
        return sum(i.subtotal() for i in self.items)

    def total(self):
        return price_order(self.subtotal(), self.discount_pct)
