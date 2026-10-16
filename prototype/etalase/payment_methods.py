"""Which ways to pay exist, and the decision behind the list.

The pilot accepts QRIS only. Cash on delivery (COD) is not offered, on purpose, for these reasons:

  * Etalase's promise to a seller is "paid before you pack". With COD the seller packs and ships on a promise, and a refused
    parcel costs the seller the outbound and return shipping with no payment to cover it.
  * The payout hold, the return window and the refund flow all assume the money arrived before the parcel left.
  * The courier collects COD cash and remits it to the seller on its own schedule, outside anything Etalase can see or hold.
  * We do not know how many buyers would leave without COD. The pilot measures that instead of guessing: the orders that expire
    unpaid, per door and per referrer, are in the operator report (GET /tasks/attribution) as `expired_unpaid` and `unpaid_rate`.

If that rate is high in the pilot, COD is a decision to reopen with data (docs/adr/0009-qris-only-no-cod.md), not a bug to patch.
"""

from __future__ import annotations

from .models import CheckoutError

ACCEPTED = ("qris",)


def view() -> dict:
    """What an agent or a page is told, so nobody promises a buyer something the platform will not do."""
    return {"methods": list(ACCEPTED), "cash_on_delivery": False,
            "note": "Payment is by QRIS, before the seller packs the order. Cash on delivery is not offered."}


def require(method: str) -> str:
    """The method a caller asked for, or a clear error an agent can read back to the buyer."""
    m = (method or "qris").strip().lower()
    if m in ACCEPTED:
        return m
    if m in ("cod", "cash", "cash_on_delivery", "bayar_di_tempat"):
        raise CheckoutError("cash on delivery is not offered: payment is by QRIS, before the seller packs the order")
    raise CheckoutError(f"payment method {method[:20]!r} is not available; payment is by QRIS")
