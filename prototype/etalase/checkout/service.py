"""Checkout & orders: reservation, seller confirmation, payment, shipping and payout.

Order lifecycle

  instant:        AWAITING_PAYMENT -> PAID -> ACCEPTED -> SHIPPED -> DELIVERED -> PAID_OUT
  confirm_first:  AWAITING_SELLER_CONFIRMATION -> AWAITING_PAYMENT -> ACCEPTED -> SHIPPED -> ...
  exits:          REJECTED (seller says out of stock), EXPIRED (unpaid), REFUNDED (rejected after payment,
                  parcel returned by the courier, or a return the seller accepted)

The payout does not follow delivery at once. DELIVERED starts the return window (payout_hold_hours); the money is
released when it ends and no return is open. The return states (RETURN_REQUESTED -> RETURN_APPROVED | RETURN_DISPUTED
-> REFUNDED, or back to DELIVERED) live in checkout/returns.py.
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
                      ReturnTerms, SellMode, StockOwner, now)
from ..security import RateLimiter
from ..store import Store

log = logging.getLogger("etalase.checkout")

PHONE_RE = re.compile(r"^\+?[0-9]{8,15}$")
POSTAL_RE = {"ID": re.compile(r"^[0-9]{5}$")}
LIMITS = {"name": 80, "line1": 200, "city": 60}


def normalize_phone(phone: str) -> str:
    """Digits only, with a leading 0 read as Indonesia's 62, so 0857..., +62 857... and 62857... compare equal."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    return "62" + digits[1:] if digits.startswith("0") else digits


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
        rates = self._quote(seller.origin.postal_code if seller.origin else "", postal_code,
                            max(1, v.weight_gram) * qty, v.price * qty)
        return [r.model_dump() for r in sorted(rates, key=lambda r: r.price)]

    def _quote(self, origin_postal: str, dest_postal: str, weight_gram: int, value: int):
        """Courier rates, with a provider outage (or an empty Biteship balance) reported as a normal checkout error
        instead of a server error the buyer's agent cannot act on."""
        try:
            return self.shipping.get_rates(origin_postal, dest_postal, weight_gram, value)
        except Exception:
            log.exception("courier rates failed")
            raise CheckoutError("courier rates are unavailable right now; try again in a minute") from None

    # ------------------------------------------------------------------ checkout
    def create_checkout(self, product_id: str, variant_id: str, qty: int, buyer: Address,
                        courier: str | None = None, service: str | None = None) -> Order:
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

        rates = self._quote(seller.origin.postal_code if seller.origin else "", buyer.postal_code,
                            max(1, v.weight_gram) * qty, v.price * qty)
        if courier:
            chosen = [r for r in rates if r.courier == courier.lower()]
            if not chosen:
                raise CheckoutError(f"courier {courier} is not available; options: {', '.join(sorted({r.courier for r in rates}))}")
            rates = chosen
        if service:
            by_service = [r for r in rates if r.service.lower() == service.lower()]
            if not by_service:
                raise CheckoutError(f"service {service} is not available for this route")
            rates = by_service
        if not rates:
            raise CheckoutError("no courier serves this route")
        rate = min(rates, key=lambda r: r.price)

        subtotal = v.price * qty
        order = Order(
            seller_id=seller.id, buyer=buyer, sell_mode=mode,
            status=OrderStatus.AWAITING_SELLER_CONFIRMATION if mode == SellMode.CONFIRM_FIRST else OrderStatus.AWAITING_PAYMENT,
            items=[OrderItem(product_id=product.id, variant_id=v.id, sku=v.sku,
                             title=f"{product.title} ({v.label})", qty=qty, unit_price=v.price,
                             weight_gram=max(1, v.weight_gram))],
            currency=COUNTRY_PACKS[buyer.country].currency,
            subtotal=subtotal, shipping_rate=rate, shipping_fee=rate.price, total=subtotal + rate.price,
        )
        order.return_terms = ReturnTerms(window_hours=self.hold_hours(seller),
                                         accepts_change_of_mind=seller.return_policy.accepts_change_of_mind)
        ttl = self.settings.seller_confirm_timeout_minutes if mode == SellMode.CONFIRM_FIRST else self.settings.reservation_ttl_minutes
        limit = getattr(self.payment, "max_amount", None)   # QRIS caps a single payment (Xendit: Rp10,000,000)
        if limit and order.total > limit:
            raise CheckoutError(f"orders above Rp{limit:,} cannot be paid by QRIS; reduce the quantity".replace(",", "."))
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

    def _refund(self, order: Order, reason: str, amount: int | None = None) -> Order:
        """Money back to the buyer: all of it, or `amount`. Never loses track of a refund the provider cannot do yet."""
        amount = order.total if amount is None else amount
        try:
            self.payment.refund(order, amount)
            status = "refunded"
            note = f"refunded ({reason})" if amount == order.total else f"refunded {amount} of {order.total} ({reason})"
        except Exception as e:  # ManualActionRequired or a provider error: never lose track of the money
            if not isinstance(e, ManualActionRequired):
                log.exception("refund failed for %s", order.id)
            status, note = "refund_pending", f"manual refund required ({reason}): {e}"
            self.events.publish("ops.manual_action", {"order_id": order.id, "action": "refund"})

        def mark(o: Order) -> bool:
            o.payment.status = status
            o.payment.refunded_amount = amount if status == "refunded" else 0
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
            shipment = self.shipping.book(order, seller.name, seller.whatsapp, origin.line1 if origin else "",
                                          origin.postal_code if origin else "")

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
        """Courier webhook (key = our order id, the waybill, or the aggregator's order/tracking id).
        DELIVERED starts the return window; the payout follows when it ends. RETURNED (the courier brought the parcel
        back to the seller) refunds the buyer."""
        found = self.store.get_order(key) or self._order_by_shipment_key(key)
        if found is None or found.shipment is None:
            return found
        if status == "delivered":
            # Money moves because of delivery, so the webhook is only a hint: ask the courier first.
            if found.status != OrderStatus.SHIPPED or not self.shipping.confirm_delivered(found):
                log.warning("delivery for %s not confirmed by the courier; ignoring", found.id)
                return found
        if status == "returned":
            # A refund is money moving as well: ask the courier whether the parcel is really back.
            if found.status != OrderStatus.SHIPPED or not self.shipping.confirm_returned(found):
                log.warning("return of %s not confirmed by the courier; ignoring", found.id)
                return found
        hold = timedelta(0)
        if status == "delivered":
            # The window the buyer was promised at checkout; orders from before returns existed use today's rule.
            terms = found.return_terms
            seller = self.store.get_seller(found.seller_id)
            hold = timedelta(hours=terms.window_hours if terms else self.hold_hours(seller))
        what: dict[str, bool] = {}

        def update(o: Order) -> bool:

            what.clear()  # Firestore may retry callbacks
            if o.shipment.status in ("delivered", "returned"):
                return False  # late or out-of-order courier events never move status backwards
            previous, o.shipment.status = o.shipment.status, status
            if status == "failed":
                o.log("the courier could not deliver this parcel (cancelled, rejected or lost); the seller must follow up")
                what["failed"] = previous != "failed"
            if status == "delivered" and o.status == OrderStatus.SHIPPED:
                o.status, o.delivered_at = OrderStatus.DELIVERED, now()
                o.payout_due_at = o.delivered_at + hold
                o.log(f"delivered (confirmed with courier); payout due {o.payout_due_at.isoformat(timespec='seconds')}")
                what["delivered"] = True
            if status == "returned" and o.status == OrderStatus.SHIPPED:
                o.status = OrderStatus.REFUNDED
                o.log("the courier brought the parcel back to the seller; refunding the buyer")
                what["returned"] = True
            return True
        order = self.store.update_order(found.id, update)
        if what.get("failed"):
            self.events.publish("ops.manual_action", {"order_id": order.id, "action": "undelivered_parcel"})
        if what.get("returned"):
            order = self._refund(order, "parcel returned by the courier")      # money first: a stock error must not skip it
            self._restock(order.id)                                             # the goods are back on the seller's shelf
            self.events.publish("order.returned_to_sender", {"order_id": order.id})
            return order
        if what.get("delivered"):
            self.events.publish("order.delivered", {"order_id": order.id})
            return self.release_payout(order.id)   # releases at once only when there is no hold
        return order

    # ------------------------------------------------------------------ payout
    def hold_hours(self, seller) -> int:
        """How long the payout waits after delivery (= the buyer's return window). A seller with few completed orders
        waits longer: an unproven seller is where a refund is most likely."""
        s = self.settings
        if s.payout_hold_hours <= 0:
            return 0
        done = sum(1 for o in self.store.list_orders(seller.id) if self.completed(o))
        return max(s.payout_hold_hours, s.new_seller_hold_hours) if done < s.new_seller_orders else s.payout_hold_hours

    @staticmethod
    def completed(o: Order) -> bool:
        """A sale that went all the way: paid out, or its payout claimed and waiting for an operator (Xendit payouts are
        manual today and stay `delivered` with payout.status pending_manual)."""
        return o.status == OrderStatus.PAID_OUT or (o.status == OrderStatus.DELIVERED and o.payout is not None)

    def _restock(self, order_id: str) -> None:
        try:
            self.store.restock(order_id)
        except Exception:
            log.exception("restock failed for %s; the stock needs a manual correction", order_id)
            self.events.publish("ops.manual_action", {"order_id": order_id, "action": "restock"})

    def track_record(self, seller_id: str) -> dict:
        """What a buyer's agent may weigh: how many orders this seller completed and how many ended in a refunded return.
        Counted from the orders on every read; production keeps these as counters on the seller document."""
        orders = self.store.list_orders(seller_id)
        return {"orders_completed": sum(1 for o in orders if self.completed(o)),
                "returns_refunded": sum(1 for o in orders if o.return_request and o.return_request.outcome == "refunded")}

    def release_payout(self, order_id: str, when: datetime | None = None) -> Order:
        when = when or now()
        order = self.store.get_order(order_id)
        if order.status != OrderStatus.DELIVERED or order.payout is not None:
            return order
        if order.payout_due_at and order.payout_due_at > when:
            return order   # still inside the return window
        seller = self.store.get_seller(order.seller_id)
        rate = seller.commission_rate if seller.commission_rate is not None else self.settings.commission_rate
        commission = round(order.subtotal * rate)
        seller_amount = order.subtotal - commission  # the shipping fee goes to the courier

        def claim(o: Order) -> bool:  # exactly one caller may release money
            if o.status != OrderStatus.DELIVERED or o.payout is not None:
                return False
            if o.payout_due_at and o.payout_due_at > when:
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

    def release_due_payouts(self, when: datetime | None = None) -> list[str]:
        """Pay out every delivered order whose return window has ended and that has no return open."""
        when = when or now()
        released = []
        for o in self.store.orders_in_status([OrderStatus.DELIVERED]):
            if o.payout is not None or (o.payout_due_at and o.payout_due_at > when):
                continue
            try:
                if self.release_payout(o.id, when).status == OrderStatus.PAID_OUT:
                    released.append(o.id)
            except Exception:    # one broken order must not stop the payouts of everyone else
                log.exception("payout sweep failed for %s", o.id)
        return released

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
            try:
                order = self.store.update_order(res.order_id, expire)
                if order is None or order.status in (OrderStatus.EXPIRED, OrderStatus.REJECTED):
                    self.store.release(res.order_id)  # also cleans up holds left by a crash
                if won:
                    self.events.publish("order.expired", {"order_id": order.id})
                    expired.append(order.id)
            except Exception:    # one broken order must not stop the job for everyone else
                log.exception("expiry failed for %s", res.order_id)
        return expired

    def cancel_unpaid(self, order_id: str, reason: str = "cancelled by the buyer's agent") -> Order | None:
        """Give the stock back for a checkout nobody has paid. A paid order is never touched here."""
        won = {}

        def cancel(o: Order) -> bool:
            won.clear()  # Firestore may run a transaction callback more than once
            if o.status not in (OrderStatus.AWAITING_PAYMENT, OrderStatus.AWAITING_SELLER_CONFIRMATION):
                return False
            o.status = OrderStatus.EXPIRED
            o.log(f"{reason}, stock released")
            won["yes"] = True
            return True
        order = self.store.update_order(order_id, cancel)
        if order is not None and order.status == OrderStatus.EXPIRED:
            self.store.release(order_id)
            if won:
                self.events.publish("order.expired", {"order_id": order.id})
        return order

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

    def _order_by_shipment_key(self, key: str) -> Order | None:
        # Prototype scan. Production: Firestore queries on shipment.waybill / provider_order_id / tracking_id.
        if not key:
            return None
        for seller_id in {p.seller_id for p in self.store.list_products()}:
            for o in self.store.list_orders(seller_id):
                sh = o.shipment
                if sh and key in (sh.waybill, sh.provider_order_id, sh.tracking_id):
                    return o
        return None
