from .inventory import release, reserve


class Order:
    def __init__(self, cart, receipt):
        self.cart = cart
        self.receipt = receipt

    def total(self):
        return self.receipt["amount"]


def place_order(cart, catalog, payment):
    reserved = []
    try:
        for item in cart.items:
            reserve(catalog, item.product.sku, item.qty)
            reserved.append(item)
        receipt = payment.pay(cart.total())
    except Exception:
        for item in reserved:
            release(catalog, item.product.sku, item.qty)
        raise
    return Order(cart, receipt)
