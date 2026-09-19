import sqlite3

import pytest

from ecommerce.api import handle
from ecommerce.cart import Cart
from ecommerce.catalog import Catalog
from ecommerce.models import Product
from ecommerce.orders import place_order
from ecommerce.payments import CardPayment, make_payment
from ecommerce.pricing import apply_discount, price_order, shipping, tax


def catalog():
    c = Catalog()
    c.add(Product("A1", "Lamp", 100.0, stock=5))
    c.add(Product("B2", "Desk", 450.0, stock=1))
    return c


def test_pricing_rules():
    assert apply_discount(200, 10) == 180
    assert tax(100) == 18.0
    assert shipping(499) == 40 and shipping(500) == 0
    assert price_order(100, 0) == 158.0          # 100 + 18 tax + 40 shipping


def test_cart_total():
    c = catalog()
    cart = Cart()
    cart.add(c.get("A1"), 2)
    assert cart.subtotal() == 200.0
    assert cart.total() == 276.0                  # 200 + 36 tax + 40 shipping


def test_place_order_reserves_stock_and_pays():
    c = catalog()
    cart = Cart()
    cart.add(c.get("A1"), 2)
    order = place_order(cart, c, CardPayment("4111111111111111"))
    assert order.total() == 276.0 and c.get("A1").stock == 3


def test_out_of_stock_rolls_back():
    c = catalog()
    cart = Cart()
    cart.add(c.get("A1"), 1)
    cart.add(c.get("B2"), 2)                      # only 1 desk in stock
    with pytest.raises(ValueError):
        place_order(cart, c, CardPayment("4111111111111111"))
    assert c.get("A1").stock == 5


def test_api_checkout_and_listing():
    c = catalog()
    out = handle({"path": "/checkout", "catalog": c, "body": {"items": [("A1", 1)], "method": "upi", "detail": "me@bank"}})
    assert out["status"] == "ok"
    assert handle({"path": "/products", "catalog": c}) == {"products": ["Lamp", "Desk"]}


def test_api_search():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE products (sku TEXT, name TEXT, price REAL, stock INT)")
    db.execute("INSERT INTO products VALUES ('A1', 'Lamp', 100, 5)")
    out = handle({"path": "/search", "db": db, "query": {"q": "Lam"}})
    assert out["results"] == [("A1", "Lamp", 100.0, 5)]


def test_make_payment():
    assert make_payment("card", "4111111111111111").pay(10)["last4"] == "1111"
