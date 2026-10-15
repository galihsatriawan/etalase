"""Checkout & orders: reservation, seller confirmation, payment, shipping and payout.

Order lifecycle

  instant:        AWAITING_PAYMENT -> PAID -> ACCEPTED -> SHIPPED -> DELIVERED -> PAID_OUT
  confirm_first:  AWAITING_SELLER_CONFIRMATION -> AWAITING_PAYMENT -> ACCEPTED -> SHIPPED -> ...
  exits:          REJECTED (seller says out of stock), EXPIRED (unpaid), REFUNDED (rejected after payment)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from ..adapters.payment import ManualActionRequired, PaymentProvider
from ..adapters.shipping import ShippingProvider
from ..catalog.service import CatalogService
from ..config import COUNTRY_PACKS, Settings
from ..events import EventBus
from ..models import (Address, CheckoutError, Order, OrderItem, OrderStatus, OutOfStock, Payout, ProductStatus,
                      SellMode, StockOwner, now)
from ..security import RateLimiter
from ..store import Store

log = logging.getLogger("etalase.checkout")

PHONE_RE = re.compile(r"^\+?[0-9]{8,15}$")
POSTAL_RE = {"ID": re.compile(r"^[0-9]{5}$")}
LIMITS = {"name": 80, "line1": 200, "city": 60}


def validate_buyer(buyer: Address) -> None:
    """Agents pass user-typed data straight through, so every field is checked here."""
    if not buyer.name.strip():
        raise CheckoutError("buyer name is required")
    for field, limit in LIMITS.items():
        if len(getattr(buyer, field)) > limit:
            raise CheckoutError(f"{field} is longer than {limit} characters")
    if not PHONE_RE.match(buyer.phone.replace(" ", "").replace("-", "")):
        raise CheckoutError("buyer phone must be 8-15 digits")
    if len(buyer.line1.strip()) < 5:
        raise CheckoutError("a full delivery address is required")
    pattern = POSTAL_RE.get(buyer.country)
    if not buyer.postal_code or (pattern and not pattern.match(buyer.postal_code)):
        raise CheckoutError("a valid buyer postal code is required for courier rates")


class CheckoutService:
    def __init__(self, store: Store, settings: Settings, catalog: CatalogService, payment: PaymentProvider,
                 shipping: ShippingProvider, events: EventBus) -> None:
        self.store, self.settings, self.catalog = store, settings, catalog
        self.payment, self.shipping, self.events = payment, shipping, events
        self.limits = RateLimiter()

    # ------------------------------------------------------------------ quotes
    def shipping_options(self, product_id: str, variant_id: str, qty: int, postal_code: str) -> list[dict]:
        product = self._published(product_id)
        seller = self.store.get_seller(product.seller_id)
        v = product.variant(variant_id)
        rates = self.shipping.get_rates(seller.origin.postal_code if seller.origin else "", postal_code,
                                        max(1, v.weight_gram) * qty, v.price * qty)
        return [r.model_dump() for r in sorted(rates, key=lambda r: r.price)]

    # ------------------------------------------------------------------ checkout
    def create_checkout(self, product_id: str, variant_id: str, qty: int, buyer: Address,
                        courier: str | None = None) -> Order:
        if qty <= 0:
            raise CheckoutError("quantity must be at least 1")
        if qty > self.settings.max_qty_per_order:
            raise CheckoutError(f"at most {self.settings.max_qty_per_order} units per checkout")
        product = self._published(product_id)
        seller = self.store.get_seller(product.seller_id)
        if seller is None or seller.kyc_status != "verified":
            raise CheckoutError("this seller is not accepting orders yet")
        if buyer.country not in product.ships_to or buyer.country not in COUNTRY_PACKS:
            raise CheckoutError(f"shipping to {buyer.country} is not available (domestic delivery only)")
        validate_buyer(buyer)
        phone = buyer.phone.lstrip("+").replace(" ", "").replace("-", "")
        if not self.limits.allow(f"buyer:{phone}", self.settings.max_checkouts_per_buyer_per_hour, 3600):
            raise CheckoutError("too many checkouts for this phone number; try again later")
        v = product.variant(variant_id)
        mode = self.catalog.sell_mode(product, v, seller)

        rates = self.shipping.get_rates(seller.origin.postal_code if seller.origin else "", buyer.postal_code,
                                        max(1, v.weight_gram) * qty, v.price * qty)
        if courier:
            chosen = [r for r in rates if r.courier == courier.lower()]
            if not chosen:
                raise CheckoutError(f"courier {courier} is not available; options: {', '.join(sorted({r.courier for r in rates}))}")
            rates = chosen
        if not rates:
            raise CheckoutError("no courier serves this route")
        rate = min(rates, key=lambda r: r.price)

        subtotal = v.price * qty
        order = Order(
            seller_id=seller.id, buyer=buyer, sell_mode=mode,
            status=OrderStatus.AWAITING_SELLER_CONFIRMATION if mode == SellMode.CONFIRM_FIRST else OrderStatus.AWAITING_PAYMENT,
            items=[OrderItem(product_id=product.id, variant_id=v.id, sku=v.sku,
                             title=f"{product.title} ({v.label})", qty=qty, unit_price=v.price)],
            currency=COUNTRY_PACKS[buyer.country].currency,
            subtotal=subtotal, shipping_rate=rate, shipping_fee=rate.price, total=subtotal + rate.price,
        )
        ttl = self.settings.seller_confirm_timeout_minutes if mode == SellMode.CONFIRM_FIRST else self.settings.reservation_ttl_minutes
        order.expires_at = now() + timedelta(minutes=ttl)
        try:
            self.store.reserve(order.id, product.id, v.id, qty, order.expires_at)
        except OutOfStock as e:
            raise CheckoutError(f"not enough stock: {e}") from e
        order.log(f"created in {mode.value} mode, stock reserved")

        if mode == SellMode.INSTANT:
            try:
                order.payment = self.payment.create_payment(order, seller.payout_account_id)
            except Exception as e:  # never leave units held for a checkout that cannot be paid
                self.store.release(order.id)
                log.exception("create_payment failed for %s", order.id)
                raise CheckoutError("payment is temporarily unavailable, please try again") from e
            order.log("payment requested")
            self.store.put_order(order)
            self.events.publish("order.awaiting_payment", {"order_id": order.id})
        else:
            self.store.put_order(order)
            self.events.publish("order.awaiting_confirmation", {"order_id": order.id})
        return order

    def seller_confirms_stock(self, seller_id: str, order_id: str, available: bool) -> Order:
        order = self._seller_order(seller_id, order_id)
        if order.status != OrderStatus.AWAITING_SELLER_CONFIRMATION:
            return order
        if not available:
            def reject(o: Order) -> bool:
                if o.status != OrderStatus.AWAITING_SELLER_CONFIRMATION:
                    return False
                o.status = OrderStatus.REJECTED
                o.log("seller: out of stock")
                return True
            updated = self.store.update_order(order.id, reject)
            if updated.status == OrderStatus.REJECTED:
                self.store.release(order.id)
                seller = self.store.get_seller(seller_id)
                if seller and seller.stock_owner == StockOwner.INTERNAL:  # the seller just told us it is gone
                    for item in order.items:
                        self.store.set_stock(seller_id, item.product_id, item.variant_id, 0, "seller_confirmation")
                self.events.publish("order.rejected", {"order_id": order.id, "paid": False})
            return updated
        expires_at = now() + timedelta(minutes=self.settings.reservation_ttl_minutes)
        draft = order.model_copy(deep=True)
        draft.expires_at = expires_at
        seller = self.store.get_seller(seller_id)
        try:
            payment = self.payment.create_payment(draft, seller.payout_account_id if seller else None)
        except Exception:
            log.exception("create_payment failed for %s", order.id)
            raise CheckoutError("payment is temporarily unavailable; tap Available again in a minute")

        won = {}

        def confirm(o: Order) -> bool:

            won.clear()  # Firestore may run a transaction callback more than once
            if o.status != OrderStatus.AWAITING_SELLER_CONFIRMATION:
                return False  # expired or answered meanwhile
            o.seller_confirmed, o.status, o.expires_at, o.payment = True, OrderStatus.AWAITING_PAYMENT, expires_at, payment
            o.log("seller confirmed stock, payment requested")
            won["yes"] = True
            return True

        updated = self.store.update_order(order.id, confirm)
        if won:
            self.store.extend(order.id, expires_at)  # the hold must outlive the QR code
            self.events.publish("order.awaiting_payment", {"order_id": order.id})
        return updated

    def payment_succeeded(self, reference: str) -> Order | None:
        """Payment webhook. Idempotent: providers may deliver the same event twice."""
        found = self.store.find_order_by_payment_ref(reference)
        if found is None:
            return None
        outcome = {}

        def pay(o: Order) -> bool:
            outcome.clear()  # Firestore may run a transaction callback more than once
            if o.status == OrderStatus.AWAITING_PAYMENT:
                o.payment.status = "paid"
                # In confirm-first mode the seller already confirmed, so the order is accepted.
                o.status = OrderStatus.ACCEPTED if o.seller_confirmed else OrderStatus.PAID
                o.log("payment received")
                outcome["paid"] = True
                return True
            if o.status == OrderStatus.EXPIRED and o.payment and o.payment.status == "pending":
                o.payment.status = "paid_late"  # paid after the hold ended: stock is gone, give the money back
                o.log("payment arrived after expiry, refunding")
                outcome["late"] = True
                return True
            return False  # duplicate webhook

        order = self.store.update_order(found.id, pay)
        if outcome.get("paid"):
            self.store.commit(order.id)
            self.events.publish("order.paid", {"order_id": order.id})
        elif outcome.get("late"):
            order = self._refund(order, "late payment")
        return order

    def _refund(self, order: Order, reason: str) -> Order:
        try:
            self.payment.refund(order)
            status, note = "refunded", f"refunded ({reason})"
        except Exception as e:  # ManualActionRequired or a provider error: never lose track of the money
            if not isinstance(e, ManualActionRequired):
                log.exception("refund failed for %s", order.id)
            status, note = "refund_pending", f"manual refund required ({reason}): {e}"
            self.events.publish("ops.manual_action", {"order_id": order.id, "action": "refund"})

        def mark(o: Order) -> bool:
            o.payment.status = status
            o.log(note)
            return True
        return self.store.update_order(order.id, mark)

    def seller_accepts(self, seller_id: str, order_id: str) -> Order:
        self._seller_order(seller_id, order_id)
        won = {}

        def accept(o: Order) -> bool:

            won.clear()  # Firestore may run a transaction callback more than once
            if o.status != OrderStatus.PAID:
                return False
            o.status = OrderStatus.ACCEPTED
            o.log("seller accepted")
            won["yes"] = True
            return True
        order = self.store.update_order(order_id, accept)
        if won:
            self.events.publish("order.accepted", {"order_id": order.id})
        return order

    def seller_rejects_paid(self, seller_id: str, order_id: str) -> Order:
        self._seller_order(seller_id, order_id)
        won = {}

        def reject(o: Order) -> bool:

            won.clear()  # Firestore may run a transaction callback more than once
            if o.status not in (OrderStatus.PAID, OrderStatus.ACCEPTED):
                return False
            o.status = OrderStatus.REFUNDED
            o.log("seller rejected after payment")
            won["yes"] = True
            return True
        order = self.store.update_order(order_id, reject)
        if not won:
            return order
        self.store.restock(order.id)
        seller = self.store.get_seller(seller_id)
        if seller and seller.stock_owner == StockOwner.INTERNAL:  # an OMS or store owns its own stock
            for item in order.items:
                self.store.set_stock(seller_id, item.product_id, item.variant_id, 0, "seller_rejection")
        order = self._refund(order, "seller out of stock")
        self.events.publish("order.rejected", {"order_id": order.id, "paid": True})
        return order

    def mark_shipped(self, seller_id: str, order_id: str | None, waybill: str | None) -> Order:
        """Seller sends a tracking number in WhatsApp. If no order id, use their oldest accepted order."""
        if order_id:
            order = self._seller_order(seller_id, order_id)
        else:
            open_orders = sorted((o for o in self.store.list_orders(seller_id) if o.status == OrderStatus.ACCEPTED),
                                 key=lambda o: o.created_at)
            if not open_orders:
                raise CheckoutError("no accepted order waiting for shipment")
            order = open_orders[0]
        if order.status != OrderStatus.ACCEPTED:
            raise CheckoutError(f"order is {order.status.value}, not ready to ship")
        seller = self.store.get_seller(seller_id)
        if waybill:
            from ..models import Shipment
            waybill = waybill.strip().upper()
            if not re.fullmatch(r"[A-Z0-9-]{6,40}", waybill):
                raise CheckoutError("that does not look like a tracking number")
            shipment = Shipment(courier=order.shipping_rate.courier, service=order.shipping_rate.service,
                                waybill=waybill, status="picked_up")
        else:
            origin = seller.origin
            shipment = self.shipping.book(order, seller.name, seller.whatsapp, origin.line1 if origin else "")

        def ship(o: Order) -> bool:
            if o.status != OrderStatus.ACCEPTED:
                return False
            o.shipment, o.status = shipment, OrderStatus.SHIPPED
            o.log(f"shipped, waybill {shipment.waybill}")
            return True
        order = self.store.update_order(order.id, ship)
        if order.status != OrderStatus.SHIPPED:
            raise CheckoutError(f"order is {order.status.value}, not ready to ship")
        self.events.publish("order.shipped", {"order_id": order.id})
        return order

    def shipment_update(self, key: str, status: str) -> Order | None:
        """Courier webhook (key = waybill or order id). DELIVERED releases the payout."""
        found = self.store.get_order(key) or self._order_by_waybill(key)
        if found is None or found.shipment is None:
            return found
        if status == "delivered":
            # Money moves on delivery, so the webhook is only a hint: ask the courier first.
            if found.status != OrderStatus.SHIPPED or not self.shipping.confirm_delivered(found):
                log.warning("delivery for %s not confirmed by the courier; ignoring", found.id)
                return found
        delivered = {}

        def update(o: Order) -> bool:

            delivered.clear()  # Firestore may retry callbacks
            if o.shipment.status in ("delivered", "returned"):
                return False  # late or out-of-order courier events never move status backwards
            o.shipment.status = status
            if status == "delivered" and o.status == OrderStatus.SHIPPED:
                o.status = OrderStatus.DELIVERED
                o.log("delivered (confirmed with courier)")
                delivered["yes"] = True
            return True
        order = self.store.update_order(found.id, update)
        if delivered:
            self.events.publish("order.delivered", {"order_id": order.id})
            return self.release_payout(order.id)
        return order

    def release_payout(self, order_id: str) -> Order:
        order = self.store.get_order(order_id)
        if order.status != OrderStatus.DELIVERED or order.payout is not None:
            return order
        seller = self.store.get_seller(order.seller_id)
        rate = seller.commission_rate if seller.commission_rate is not None else self.settings.commission_rate
        commission = round(order.subtotal * rate)
        seller_amount = order.subtotal - commission  # the shipping fee goes to the courier

        def claim(o: Order) -> bool:  # exactly one caller may release money
            if o.status != OrderStatus.DELIVERED or o.payout is not None:
                return False
            o.payout = Payout(seller_amount=seller_amount, commission=commission, status="releasing")
            return True
        claimed = self.store.update_order(order_id, claim)
        if claimed.payout is None or claimed.payout.status != "releasing":
            return claimed
        try:
            self.payment.release_to_seller(claimed, seller.payout_account_id, seller_amount, commission)
            payout_status, new_status, note = "released", OrderStatus.PAID_OUT, "payout released"
        except Exception as e:  # ManualActionRequired or a provider error: an operator finishes it
            if not isinstance(e, ManualActionRequired):
                log.exception("payout failed for %s", order_id)
            payout_status, new_status, note = "pending_manual", OrderStatus.DELIVERED, f"manual payout required: {e}"
            self.events.publish("ops.manual_action", {"order_id": order_id, "action": "payout"})

        def finish(o: Order) -> bool:
            o.payout.status, o.status = payout_status, new_status
            o.log(f"{note}: {seller_amount} to seller, {commission} commission")
            return True
        order = self.store.update_order(order_id, finish)
        if payout_status == "released":
            self.events.publish("payout.released", {"order_id": order.id})
        return order

    def expire_due(self, when: datetime | None = None) -> list[str]:
        """Release reservations that ran out of time. Production: one Cloud Task per order at expires_at."""
        when = when or now()
        expired = []
        for res in self.store.held_reservations_expiring_before(when):
            if res.order_id in expired:
                continue
            won = {}

            def expire(o: Order) -> bool:

                won.clear()  # Firestore may run a transaction callback more than once
                if o.status not in (OrderStatus.AWAITING_PAYMENT, OrderStatus.AWAITING_SELLER_CONFIRMATION):
                    return False
                if o.expires_at and o.expires_at > when:  # extended meanwhile (seller confirmed)
                    return False
                o.status = OrderStatus.EXPIRED
                o.log("expired, stock released")
                won["yes"] = True
                return True
            order = self.store.update_order(res.order_id, expire)
            if order is None or order.status in (OrderStatus.EXPIRED, OrderStatus.REJECTED):
                self.store.release(res.order_id)  # also cleans up holds left by a crash
            if won:
                self.events.publish("order.expired", {"order_id": order.id})
                expired.append(order.id)
        return expired

    # ------------------------------------------------------------------ helpers
    def _published(self, product_id: str):
        product = self.store.get_product(product_id)
        if product is None or product.status != ProductStatus.PUBLISHED:
            raise CheckoutError("product not found")
        return product

    def _seller_order(self, seller_id: str, order_id: str) -> Order:
        order = self.store.get_order(order_id)
        if order is None or order.seller_id != seller_id:  # tenant isolation
            raise CheckoutError("order not found")
        return order

    def _order_by_waybill(self, waybill: str) -> Order | None:
        # Prototype scan. Production: a Firestore query on shipment.waybill (single-field index).
        for seller_id in {p.seller_id for p in self.store.list_products()}:
            for o in self.store.list_orders(seller_id):
                if o.shipment and o.shipment.waybill == waybill:
                    return o
        return None
