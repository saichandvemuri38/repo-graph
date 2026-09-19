from .models import Product


class Catalog:
    def __init__(self):
        self._products = {}

    def add(self, product: Product):
        self._products[product.sku] = product

    def get(self, sku):
        return self._products[sku]

    def all(self):
        return list(self._products.values())

    def in_stock(self):
        return [p for p in self.all() if p.stock > 0]


def search_products(conn, name):
    """Find products whose name contains `name`."""
    query = "SELECT sku, name, price, stock FROM products WHERE name LIKE '%" + name + "%'"
    return conn.execute(query).fetchall()
