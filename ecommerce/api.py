"""A tiny request router. Handlers take a request dict and return a response dict."""

from .cart import Cart
from .catalog import search_products
from .orders import place_order
from .payments import make_payment

ROUTES = {}
ORDERS = []


def route(path):
    def register(handler):
        ROUTES[path] = handler
        return handler
    return register


@route("/products")
def list_products(request):
    return {"products": [p.name for p in request["catalog"].in_stock()]}


@route("/orders")
def list_orders(request=None):
    return {"orders": ORDERS}


@route("/search")
def search(request):
    return {"results": search_products(request["db"], request["query"]["q"])}


@route("/checkout")
def checkout(request):
    cart = Cart()
    for sku, qty in request["body"]["items"]:
        cart.add(request["catalog"].get(sku), qty)
    payment = make_payment(request["body"]["method"], request["body"]["detail"])
    order = place_order(cart, request["catalog"], payment)
    ORDERS.append({
        "total": order.total(),
        "items": [{"sku": item.product.sku, "qty": item.qty} for item in cart.items],
    })
    return {"status": "ok", "total": order.total()}


def handle(request):
    return ROUTES[request["path"]](request)
