from dataclasses import dataclass


@dataclass
class Product:
    sku: str
    name: str
    price: float
    stock: int = 0


@dataclass
class CartItem:
    product: Product
    qty: int

    def subtotal(self):
        return self.product.price * self.qty
