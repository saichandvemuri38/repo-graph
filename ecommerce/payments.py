class PaymentMethod:
    def pay(self, amount):
        raise NotImplementedError


class CardPayment(PaymentMethod):
    def __init__(self, number):
        self.number = number

    def pay(self, amount):
        return {"method": "card", "last4": self.number[-4:], "amount": amount, "status": "paid"}


class UpiPayment(PaymentMethod):
    def __init__(self, handle):
        self.handle = handle

    def pay(self, amount):
        return {"method": "upi", "handle": self.handle, "amount": amount, "status": "paid"}


PAYMENT_METHODS = {"card": CardPayment, "upi": UpiPayment}


def make_payment(kind, detail):
    return PAYMENT_METHODS[kind](detail)
