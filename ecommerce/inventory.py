def reserve(catalog, sku, qty):
    product = catalog.get(sku)
    if product.stock < qty:
        raise ValueError(f"only {product.stock} of {sku} left")
    product.stock -= qty


def release(catalog, sku, qty):
    catalog.get(sku).stock += qty
