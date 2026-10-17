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

from .. import attribution, payment_methods as methods
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
    def create_checkout(self, product_id: str | None = None, variant_id: str | None = None, qty: int = 1,
                        buyer: Address | None = None, courier: str | None = None, service: str | None = None,
                        channel: str = "", referrer: str = "", payment_method: str = "qris",
                        items: list[tuple[str, str, int] | dict] | None = None) -> Order:
        methods.require(payment_method)      # QRIS only in the pilot; a request for COD gets an answer the agent can repeat
        if buyer is None:
            raise CheckoutError("buyer address is required")

        raw_items = items if items is not None else [(product_id, variant_id, qty)]
        if not raw_items:
            raise CheckoutError("at least one item required")

        parsed_items: list[tuple[Product, Variant, int]] = []
        common_seller_id: str | None = None
        total_units = 0

        for it in raw_items:
            if isinstance(it, (tuple, list)):
                if len(it) < 2:
                    raise CheckoutError("invalid item specification: tuple must contain at least (product_id, variant_id)")
                p_id, v_id = it[0], it[1]
                q = it[2] if len(it) > 2 else 1
            elif isinstance(it, dict):
                p_id = it.get("product_id")
                v_id = it.get("variant_id")
                q = it.get("qty", 1)
            else:
                raise CheckoutError("invalid item specification: expected tuple/list or dict")
            if not p_id or not v_id:
                raise CheckoutError("product_id and variant_id are required for all items")
            if not isinstance(q, int) or q <= 0:
                raise CheckoutError(f"quantity must be at least 1 (got {q})")
            total_units += q
            prod = self._published(p_id)
            if common_seller_id is None:
                common_seller_id = prod.seller_id
            elif common_seller_id != prod.seller_id:
                raise CheckoutError("all items in a checkout must be from the same shop")
            if buyer.country not in prod.ships_to or buyer.country not in COUNTRY_PACKS:
                raise CheckoutError(f"shipping to {buyer.country} is not available (domestic delivery only)")
            try:
                var = prod.variant(v_id)
            except KeyError:
                raise CheckoutError(f"variant '{v_id}' not found for product '{prod.title}'")
            parsed_items.append((prod, var, q))

        if total_units > self.settings.max_qty_per_order:
            raise CheckoutError(f"at most {self.settings.max_qty_per_order} units per checkout")

        seller = self.store.get_seller(common_seller_id)
        if seller is None or seller.kyc_status != "verified":
            raise CheckoutError("this seller is not accepting orders yet")

        validate_buyer(buyer)
        phone = buyer.phone.lstrip("+").replace(" ", "").replace("-", "")
        if not self.limits.allow(f"buyer:{phone}", self.settings.max_checkouts_per_buyer_per_hour, 3600):
            raise CheckoutError("too many checkouts for this phone number; try again later")

        # If any variant requires confirmation, the order is confirm-first
        modes = [self.catalog.sell_mode(p, v, seller) for p, v, q in parsed_items]
        mode = SellMode.CONFIRM_FIRST if SellMode.CONFIRM_FIRST in modes else SellMode.INSTANT

        total_weight = sum(max(1, v.weight_gram) * q for p, v, q in parsed_items)
        subtotal = sum(v.price * q for p, v, q in parsed_items)

        rates = self._quote(seller.origin.postal_code if seller.origin else "", buyer.postal_code,
                            total_weight, subtotal)
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

        order_items = [
            OrderItem(product_id=p.id, variant_id=v.id, sku=v.sku,
                      title=f"{p.title} ({v.label})", qty=q, unit_price=v.price,
                      weight_gram=max(1, v.weight_gram))
            for p, v, q in parsed_items
        ]

        order = Order(
            seller_id=seller.id, buyer=buyer, sell_mode=mode,
            status=OrderStatus.AWAITING_SELLER_CONFIRMATION if mode == SellMode.CONFIRM_FIRST else OrderStatus.AWAITING_PAYMENT,
            items=order_items,
            currency=COUNTRY_PACKS[buyer.country].currency,
            subtotal=subtotal, shipping_rate=rate, shipping_fee=rate.price, total=subtotal + rate.price,
        )
        # Where it came from (attribution.py): a fixed channel name and a short label, nothing that points at a person.
        order.channel = channel if channel in attribution.CHANNELS else ""
        order.referrer = attribution.slug(referrer)
        order.return_terms = ReturnTerms(window_hours=self.hold_hours(seller),
                                         accepts_change_of_mind=seller.return_policy.accepts_change_of_mind)
        ttl = self.settings.seller_confirm_timeout_minutes if mode == SellMode.CONFIRM_FIRST else self.settings.reservation_ttl_minutes
        limit = getattr(self.payment, "max_amount", None)   # QRIS caps a single payment (Xendit: Rp10,000,000)
        if limit and order.total > limit:
            raise CheckoutError(f"orders above Rp{limit:,} cannot be paid by QRIS; reduce the quantity".replace(",", "."))
        order.expires_at = now() + timedelta(minutes=ttl)
        for p, v, q in parsed_items:
            try:
                self.store.reserve(order.id, p.id, v.id, q, order.expires_at)
            except OutOfStock as e:
                self.store.release(order.id)
                if len(parsed_items) > 1:
                    raise CheckoutError(f"not enough stock for '{p.title} ({v.label})': requested {q}, {e}") from e
                raise CheckoutError(f"not enough stock: {e}") from e
        order.log(f"created in {mode.value} mode, stock reserved" + (f" ({order.channel}, {order.referrer or 'no referrer'})" if order.channel else ""))

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
            # The money is in and the order is paid. A stock problem here (say a variant removed by a catalog resync while
            # the buyer was paying) must not stop the seller from hearing about it: log it, flag it, carry on.
            try:
                self.store.commit(order.id)
            except Exception:
                log.exception("stock commit failed for paid order %s; the stock needs a manual correction", order.id)
                self.events.publish("ops.manual_action", {"order_id": order.id, "action": "commit_stock"})
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
        order = self._refund(order, "seller out of stock")      # money first: a stock error must not skip the refund
        self._restock(order.id)
        seller = self.store.get_seller(seller_id)
        if seller and seller.stock_owner == StockOwner.INTERNAL:  # an OMS or store owns its own stock
            for item in order.items:
                try:
                    self.store.set_stock(seller_id, item.product_id, item.variant_id, 0, "seller_rejection")
                except Exception:
                    log.exception("could not zero the stock of %s after a rejection", item.variant_id)
                    self.events.publish("ops.manual_action", {"order_id": order.id, "action": "set_stock"})
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
            # Courier updates are matched to an order by this number, so it can belong to one order only. Otherwise a seller
            # could type another shop's waybill and have that parcel's delivery start this order's payout.
            taken = self._order_by_shipment_key(waybill)
            if taken is not None and taken.id != order.id:
                raise CheckoutError("that tracking number is already used by another order")
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
        if status == "lost":
            # Same for a parcel reported lost or destroyed: the courier's own tracking record has to say so.
            if found.status != OrderStatus.SHIPPED or not self.shipping.confirm_lost(found):
                log.warning("loss of %s not confirmed by the courier; ignoring", found.id)
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
            if o.shipment.status in ("delivered", "returned", "lost"):
                return False  # late or out-of-order courier events never move status backwards
            if o.shipment.status == "returning" and status in ("booked", "picked_up", "in_transit"):
                return False  # a stale "in transit" after "on its way back"; only delivered, returned or lost can follow
            previous, o.shipment.status = o.shipment.status, status
            if status == "failed":
                o.log("the courier could not take or deliver this parcel (cancelled, rejected or no courier found); the seller must follow up")
                what["failed"] = previous != "failed"
            if status == "returning" and previous != "returning":
                o.log("the courier could not deliver and is bringing the parcel back to the seller; the buyer is refunded when it arrives")
            if status == "delivered" and o.status == OrderStatus.SHIPPED:
                o.status, o.delivered_at = OrderStatus.DELIVERED, now()
                o.payout_due_at = o.delivered_at + hold
                o.log(f"delivered (confirmed with courier); payout due {o.payout_due_at.isoformat(timespec='seconds')}")
                what["delivered"] = True
            if status == "returned" and o.status == OrderStatus.SHIPPED:
                o.status = OrderStatus.REFUNDED
                o.log("the courier brought the parcel back to the seller; refunding the buyer")
                what["returned"] = True
            if status == "lost" and o.status == OrderStatus.SHIPPED:
                o.status = OrderStatus.REFUNDED
                o.log("the courier reports the parcel lost or destroyed; refunding the buyer")
                what["lost"] = True
            return True
        order = self.store.update_order(found.id, update)
        if what.get("failed"):
            self.events.publish("ops.manual_action", {"order_id": order.id, "action": "undelivered_parcel"})
        if what.get("returned"):
            order = self._refund(order, "parcel returned by the courier")      # money first: a stock error must not skip it
            self._restock(order.id)                                             # the goods are back on the seller's shelf
            self.events.publish("order.returned_to_sender", {"order_id": order.id})
            return order
        if what.get("lost"):
            order = self._refund(order, "parcel lost by the courier")            # money first; nothing comes back to restock
            self.events.publish("order.parcel_lost", {"order_id": order.id})
            return order
        if what.get("delivered"):
            self.events.publish("order.delivered", {"order_id": order.id})
            return self.release_payout(order.id)   # releases at once only when there is no hold
        return order

    def reconcile_shipments(self, when: datetime | None = None) -> list[str]:
        """Catch up on courier events that were missed. A webhook can be lost, or can arrive before the courier's own tracking
        agrees with it and be ignored (money never moves on a webhook alone), and nothing would ever ask again. Every parcel
        still marked shipped is therefore asked about every `shipment_recheck_hours`; a delivery, a return or a loss found
        this way goes through the same checks as a webhook. Returns the orders that left the shipped state."""
        when = when or now()
        every = timedelta(hours=max(1, self.settings.shipment_recheck_hours))
        moved = []
        for o in self.store.orders_in_status([OrderStatus.SHIPPED]):
            s = o.shipment
            if s is None or (s.checked_at or s.shipped_at) > when - every:
                continue
            try:
                # Record the check first: a slow or failing courier API is retried at the next interval, not every minute.
                self.store.update_order(o.id, lambda x: setattr(x.shipment, "checked_at", when) or True)
                status = self.shipping.tracking_status(o)
                if status in ("delivered", "returned", "lost"):
                    after = self.shipment_update(o.id, status)
                    if after is not None and after.status != OrderStatus.SHIPPED:
                        moved.append(o.id)
            except Exception:    # one broken order must not stop the others
                log.exception("shipment recheck failed for %s", o.id)
        return moved

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

    def attribution_report(self) -> dict:
        """Which doors buyers use, counted from the orders: for each channel and each referrer how many checkouts were started,
        how many were paid and how many expired unpaid (the buyer left before paying). Counted on every read; at pilot size
        that is a few hundred orders, and production keeps these as counters."""
        paid_states = {OrderStatus.PAID, OrderStatus.ACCEPTED, OrderStatus.SHIPPED, OrderStatus.DELIVERED, OrderStatus.PAID_OUT,
                       OrderStatus.RETURN_REQUESTED, OrderStatus.RETURN_APPROVED, OrderStatus.RETURN_DISPUTED}
        by_channel: dict[str, dict[str, int]] = {}
        by_referrer: dict[str, dict[str, int]] = {}
        for o in self.store.orders_in_status(list(OrderStatus)):
            paid = o.payment is not None and (o.status in paid_states or o.payment.status in ("paid", "refunded", "refund_pending"))
            unpaid_end = o.status == OrderStatus.EXPIRED
            for table, key in ((by_channel, o.channel or "unknown"), (by_referrer, o.referrer or "none")):
                row = table.setdefault(key, {"started": 0, "paid": 0, "expired_unpaid": 0})
                row["started"] += 1
                row["paid"] += int(paid)
                row["expired_unpaid"] += int(unpaid_end)
        totals = {k: sum(r[k] for r in by_channel.values()) for k in ("started", "paid", "expired_unpaid")}
        # The share of checkouts that ended because nobody paid. This is the pilot's measure of what QRIS-only costs us
        # (payment_methods.py): a high rate is the signal to look at cash on delivery, per door and per referrer.
        totals["unpaid_rate"] = round(totals["expired_unpaid"] / totals["started"], 3) if totals["started"] else 0.0
        return {"totals": totals, "by_channel": by_channel, "by_referrer": by_referrer}

    def track_record(self, seller_id: str) -> dict:
        """What a buyer's agent may weigh: how many orders this seller completed and how many ended in a refunded return.
        Counted from the orders on every read; production keeps these as counters on the seller document."""
        orders = self.store.list_orders(seller_id)
        return {"orders_completed": sum(1 for o in orders if self.completed(o)),
                "returns_refunded": sum(1 for o in orders if o.return_request and o.return_request.outcome == "refunded")}

    def release_payout(self, order_id: str, when: datetime | None = None) -> Order:
        when = when or now()
        order = self.store.get_order(order_id)
        if order.status != OrderStatus.DELIVERED:
            return order
        if order.payout is not None and order.payout.status != "releasing":
            return order
        if order.payout_due_at and order.payout_due_at > when:
            return order   # still inside the return window
        seller = self.store.get_seller(order.seller_id)
        rate = seller.commission_rate if seller.commission_rate is not None else self.settings.commission_rate

        if order.payout and order.payout.status == "releasing":
            # Reconcile stuck in-flight payout from a previous crashed run
            seller_amount, commission = order.payout.seller_amount, order.payout.commission
            claimed = order
        else:
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
            if claimed is None or claimed.payout is None or claimed.payout.status != "releasing":
                return claimed or order
        try:
            self.payment.release_to_seller(claimed, seller.payout_account_id if seller else None, seller_amount, commission)
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
        """Pay out every delivered order whose return window has ended and that has no return open. Also reconciles
        any orders stuck in 'releasing' status from a previous server crash."""
        when = when or now()
        released = []
        for o in self.store.orders_in_status([OrderStatus.DELIVERED]):
            if (o.payout is not None and o.payout.status != "releasing") or (o.payout_due_at and o.payout_due_at > when):
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
