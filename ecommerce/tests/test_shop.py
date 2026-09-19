import sqlite3

import pytest

from ecommerce.api import handle
from ecommerce.cart import Cart
from ecommerce.catalog import Catalog, search_products
from ecommerce.models import Product
from ecommerce.orders import place_order
from ecommerce.payments import CardPayment, make_payment
from ecommerce.pricing import apply_coupon, apply_discount, price_order, shipping, tax


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


def test_apply_coupon_save10():
    assert apply_coupon(200, "SAVE10") == 180


def test_apply_coupon_flat50():
    assert apply_coupon(200, "FLAT50") == 150


def test_apply_coupon_flat50_never_below_zero():
    assert apply_coupon(30, "FLAT50") == 0
    assert apply_coupon(50, "FLAT50") == 0


def test_apply_coupon_unknown_code_raises():
    with pytest.raises(ValueError):
        apply_coupon(100, "BOGUS")
    with pytest.raises(ValueError):
        apply_coupon(100, "save10")               # codes are case-sensitive


def test_cart_total_uses_coupon():
    c = catalog()
    cart = Cart()
    cart.add(c.get("A1"), 2)
    cart.apply_coupon("SAVE10")
    assert cart.coupon == "SAVE10"
    assert cart.subtotal() == 200.0               # coupon does not change the subtotal
    assert cart.total() == 252.4                  # 180 + 32.4 tax + 40 shipping
    cart.apply_coupon("FLAT50")                   # a new code replaces the old one
    assert cart.total() == 217.0                  # 150 + 27 tax + 40 shipping


def test_cart_total_invalid_coupon_raises():
    c = catalog()
    cart = Cart()
    cart.add(c.get("A1"), 1)
    cart.apply_coupon("BOGUS")
    with pytest.raises(ValueError):
        cart.total()


def test_place_order_with_coupon_charges_discounted_total():
    c = catalog()
    cart = Cart()
    cart.add(c.get("A1"), 2)
    cart.apply_coupon("SAVE10")
    order = place_order(cart, c, CardPayment("4111111111111111"))
    assert order.total() == 252.4


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


def test_api_orders_tracks_completed_orders():
    import ecommerce.api as api
    api.ORDERS.clear()
    c = catalog()
    handle({"path": "/checkout", "catalog": c, "body": {"items": [("A1", 1)], "method": "upi", "detail": "me@bank"}})
    assert handle({"path": "/orders"}) == {"orders": [{"total": 158.0, "items": [{"sku": "A1", "qty": 1}]}]}


def test_api_search():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE products (sku TEXT, name TEXT, price REAL, stock INT)")
    db.execute("INSERT INTO products VALUES ('A1', 'Lamp', 100, 5)")
    out = handle({"path": "/search", "db": db, "query": {"q": "Lam"}})
    assert out["results"] == [("A1", "Lamp", 100.0, 5)]


def _products_db():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE products (sku TEXT, name TEXT, price REAL, stock INT)")
    db.execute("INSERT INTO products VALUES ('A1', 'Lamp', 100, 5)")
    db.execute("INSERT INTO products VALUES ('B2', 'Desk', 450, 1)")
    return db


def test_search_is_not_sql_injectable():
    db = _products_db()
    # Classic tautology: with string concatenation this matches every row.
    payload = "zzz' OR '1'='1"
    out = handle({"path": "/search", "db": db, "query": {"q": payload}})
    assert out["results"] == []
    # A stacked/comment payload must not break the query or alter the table.
    handle({"path": "/search", "db": db, "query": {"q": "x'; DROP TABLE products; --"}})
    assert db.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 2


def test_search_still_matches_substrings_and_quotes():
    db = _products_db()
    assert [r[1] for r in search_products(db, "es")] == ["Desk"]
    db.execute("INSERT INTO products VALUES ('C3', 'Bob''s Chair', 80, 2)")
    assert [r[1] for r in search_products(db, "Bob's")] == ["Bob's Chair"]


@pytest.mark.parametrize("qty", [0, -1, -5, 1.5, "2", None, True])
def test_cart_rejects_invalid_quantity(qty):
    cart = Cart()
    with pytest.raises(ValueError):
        cart.add(catalog().get("A1"), qty)
    assert cart.items == []


def test_checkout_negative_quantity_cannot_inflate_stock_or_refund():
    c = catalog()
    body = {"items": [("A1", -5)], "method": "card", "detail": "4111111111111111"}
    with pytest.raises(ValueError):
        handle({"path": "/checkout", "catalog": c, "body": body})
    assert c.get("A1").stock == 5


def test_make_payment():
    assert make_payment("card", "4111111111111111").pay(10)["last4"] == "1111"
