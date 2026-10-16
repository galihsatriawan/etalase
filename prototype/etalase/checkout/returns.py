"""Returns: what a buyer's agent can ask for after delivery, and how the seller and the platform answer.

The payout waits out the return window (CheckoutService.hold_hours), so a refund never has to be taken back from a
seller's balance. Inside the window a buyer's agent can open one return per order:

  DELIVERED --request--> RETURN_REQUESTED --seller approves (or stays silent)--> RETURN_APPROVED
                                          \\--seller disputes------------------> RETURN_DISPUTED --operator--> REFUNDED | DELIVERED
  RETURN_APPROVED --seller confirms the item is back--> REFUNDED
  RETURN_APPROVED --nothing arrives in time--> DELIVERED (the return lapses; payout follows)
                                               or RETURN_DISPUTED (when the buyer gave a tracking number)

Reasons: damaged, wrong_item and not_as_described are always accepted inside the window. changed_mind only when the
seller opted in (Seller.return_policy). Refunds: the full payment for the first three, the product price only (no
shipping) for a change of mind; the buyer then pays the way back. Whether the provider can send a partial refund is not
settled (see docs/05); until it is, a Xendit refund is an operator task and the order shows payment.status refund_pending.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from ..models import (DEFECT_REASONS, CheckoutError, Order, OrderStatus, ReturnPolicy, ReturnReason, ReturnRequest,
                      Seller, now)
from .service import CheckoutService, normalize_phone

log = logging.getLogger("etalase.returns")

WAYBILL_RE = re.compile(r"[A-Z0-9-]{6,40}")
MAX_NOTE, MAX_EVIDENCE, MAX_URL = 500, 5, 500
OPEN_STATES = (OrderStatus.RETURN_REQUESTED, OrderStatus.RETURN_APPROVED, OrderStatus.RETURN_DISPUTED)
REASONS = [r.value for r in ReturnReason]


def clean_evidence(urls: list[str] | None) -> list[str]:
    """Photo links from the buyer's agent. They are stored and shown to people, never fetched by the server."""
    if len(urls or []) > MAX_EVIDENCE:
        raise CheckoutError(f"at most {MAX_EVIDENCE} evidence links")
    out = []
    for url in urls or []:
        url = str(url).strip()
        if not url.startswith("https://") or len(url) > MAX_URL or any(ch.isspace() for ch in url):
            raise CheckoutError("evidence must be https links to photos (at most 5, no spaces)")
        out.append(url)
    return out


class ReturnService:
    def __init__(self, checkout: CheckoutService) -> None:
        self.co, self.store, self.settings, self.events = checkout, checkout.store, checkout.settings, checkout.events

    # ------------------------------------------------------------------ policy
    def policy_view(self, seller: Seller) -> dict:
        """The seller's return terms as an agent reads them (product results, store page, schema.org)."""
        hours = self.co.hold_hours(seller)
        if hours <= 0:
            return {"window_hours": 0, "returns": "not offered through Etalase", "reasons": [],
                    "note": seller.return_policy.note}
        reasons = [r.value for r in DEFECT_REASONS] + ([ReturnReason.CHANGED_MIND.value]
                                                       if seller.return_policy.accepts_change_of_mind else [])
        return {
            "window_hours": hours,
            "window_starts": "when the courier confirms delivery",
            "reasons": reasons,
            "refund": {"damaged / wrong item / not as described": "the full amount paid, shipping included",
                       **({"changed mind": "the product price only; the original shipping fee is not refunded"}
                          if seller.return_policy.accepts_change_of_mind else {})},
            "return_shipping": "paid by the buyer; Etalase does not reimburse it in this version",
            "seller_answers_within_hours": self.settings.return_seller_response_hours,
            "how": "Call request_return with the checkout id and the buyer's phone. Attach photo links for damaged or "
                   "wrong items. The seller's silence counts as approval.",
            "note": seller.return_policy.note,
        }

    def set_policy(self, seller_id: str, accepts_change_of_mind: bool | None = None, note: str | None = None) -> ReturnPolicy:
        seller = self.store.get_seller(seller_id)
        if seller is None:
            raise CheckoutError("seller not found")
        policy = seller.return_policy.model_copy()
        if accepts_change_of_mind is not None:
            policy.accepts_change_of_mind = bool(accepts_change_of_mind)
        if note is not None:
            policy.note = " ".join(str(note).split())[:300]
        seller.return_policy = policy
        self.store.put_seller(seller)
        return policy

    # ------------------------------------------------------------------ buyer side
    def request(self, order_id: str, buyer_phone: str, reason: str, note: str = "",
                evidence: list[str] | None = None) -> Order:
        order = self.store.get_order(order_id)
        # One answer for "no such checkout" and "not your checkout": the id alone must not confirm anything.
        if order is None or normalize_phone(order.buyer.phone) != normalize_phone(buyer_phone or "x"):
            raise CheckoutError("checkout not found")
        try:
            why = ReturnReason(str(reason).strip().lower())
        except ValueError:
            raise CheckoutError(f"reason must be one of: {', '.join(REASONS)}") from None
        note = " ".join(str(note or "").split())
        if len(note) > MAX_NOTE:
            raise CheckoutError(f"note is longer than {MAX_NOTE} characters")
        links = clean_evidence(evidence)
        seller = self.store.get_seller(order.seller_id)
        # The terms the buyer's agent saw at checkout apply, not whatever the seller has switched to since.
        offers_mind = order.return_terms.accepts_change_of_mind if order.return_terms else seller.return_policy.accepts_change_of_mind
        if why == ReturnReason.CHANGED_MIND and not offers_mind:
            raise CheckoutError("this seller does not accept returns for a change of mind; reasons accepted: "
                                + ", ".join(r.value for r in DEFECT_REASONS))
        self._check_window(order)

        respond_by = now() + timedelta(hours=self.settings.return_seller_response_hours)
        won: dict[str, bool] = {}

        def open_return(o: Order) -> bool:
            won.clear()  # Firestore may run a transaction callback more than once
            if o.status != OrderStatus.DELIVERED or o.payout is not None or o.return_request is not None:
                return False
            if o.payout_due_at and o.payout_due_at < now():
                return False
            o.status = OrderStatus.RETURN_REQUESTED
            o.return_request = ReturnRequest(reason=why, note=note, evidence=links, respond_by=respond_by)
            o.log(f"return requested by the buyer: {why.value}")
            won["yes"] = True
            return True
        updated = self.store.update_order(order.id, open_return)
        if not won:
            self._check_window(updated)   # lost a race: explain why
            raise CheckoutError("a return cannot be opened for this order")
        self.events.publish("return.requested", {"order_id": order.id})
        return updated

    def _check_window(self, order: Order) -> None:
        if order.return_request is not None:
            raise CheckoutError("a return was already opened for this order" if order.status in OPEN_STATES
                                else f"the return for this order is closed ({order.return_request.outcome or order.status.value})")
        if order.status in (OrderStatus.PAID_OUT,) or order.payout is not None:
            raise CheckoutError("the return window has closed and the seller has been paid")
        if order.status != OrderStatus.DELIVERED:
            raise CheckoutError(f"returns open after delivery; this order is {order.status.value}")
        if order.payout_due_at and order.payout_due_at < now():
            raise CheckoutError("the return window has closed")

    def add_return_tracking(self, order_id: str, buyer_phone: str, waybill: str) -> Order:
        """The buyer's agent reports the tracking number of the parcel on its way back. It does not refund anything; it
        decides what happens if the seller never confirms: an operator looks at the case instead of the return lapsing."""
        order = self.store.get_order(order_id)
        if order is None or normalize_phone(order.buyer.phone) != normalize_phone(buyer_phone or "x"):
            raise CheckoutError("checkout not found")
        waybill = (waybill or "").strip().upper()
        if not WAYBILL_RE.fullmatch(waybill):
            raise CheckoutError("that does not look like a tracking number")
        won: dict[str, bool] = {}

        def save(o: Order) -> bool:
            won.clear()
            if o.status != OrderStatus.RETURN_APPROVED or o.return_request is None:
                return False
            o.return_request.return_waybill = waybill
            o.log(f"buyer sent the item back, tracking {waybill}")
            won["yes"] = True
            return True
        updated = self.store.update_order(order.id, save)
        if not won:
            raise CheckoutError("a tracking number can be added once the seller approved the return")
        self.events.publish("return.shipped_back", {"order_id": order.id})
        return updated

    # ------------------------------------------------------------------ seller side
    def seller_responds(self, seller_id: str, order_id: str, approve: bool) -> Order:
        self._seller_order(seller_id, order_id)
        won: dict[str, bool] = {}

        def answer(o: Order) -> bool:
            won.clear()
            if o.status != OrderStatus.RETURN_REQUESTED or o.return_request is None:
                return False
            if approve:
                self._approve(o, "seller")
            else:
                self._dispute(o, "the seller disputes the return")
            won["yes"] = True
            return True
        order = self.store.update_order(order_id, answer)
        if not won:
            raise CheckoutError(f"there is no return waiting for an answer on this order (it is {order.status.value})")
        self._announce(order, auto=False)
        return order

    def seller_received(self, seller_id: str, order_id: str) -> Order:
        """The seller has the item back: refund the buyer. Also allowed after a lapse was escalated to an operator
        (the buyer had reported a tracking number): the seller's confirmation settles it."""
        order = self._seller_order(seller_id, order_id)
        escalated = (order.status == OrderStatus.RETURN_DISPUTED and order.return_request is not None
                     and bool(order.return_request.return_waybill))
        states = (OrderStatus.RETURN_APPROVED, OrderStatus.RETURN_DISPUTED) if escalated else (OrderStatus.RETURN_APPROVED,)
        return self._refund_return(order_id, "the seller confirmed the item is back", from_states=states)

    # ------------------------------------------------------------------ operator
    def disputes(self) -> list[dict]:
        out = []
        for o in self.store.orders_in_status([OrderStatus.RETURN_DISPUTED]):
            r = o.return_request
            out.append({"order_id": o.id, "seller_id": o.seller_id, "total": o.total, "reason": r.reason.value,
                        "note": r.note, "evidence": r.evidence, "return_waybill": r.return_waybill,
                        "requested_at": r.requested_at.isoformat(), "history": o.history[-6:]})
        return out

    def resolve_dispute(self, order_id: str, outcome: str) -> Order:
        """An operator decides a disputed return: refund the buyer, or release the money to the seller."""
        if outcome == "refund":
            return self._refund_return(order_id, "an operator decided for the buyer", from_states=(OrderStatus.RETURN_DISPUTED,))
        if outcome != "release":
            raise CheckoutError("outcome must be refund or release")
        won: dict[str, bool] = {}

        def release(o: Order) -> bool:
            won.clear()
            if o.status != OrderStatus.RETURN_DISPUTED or o.return_request is None:
                return False
            o.status = OrderStatus.DELIVERED
            o.return_request.outcome, o.return_request.resolved_at = "rejected", now()
            o.payout_due_at = now()
            o.log("an operator decided for the seller; the payout is released")
            won["yes"] = True
            return True
        order = self.store.update_order(order_id, release)
        if order is None:
            raise CheckoutError("order not found")
        if not won:
            raise CheckoutError(f"order is {order.status.value}, not a disputed return")
        self.events.publish("return.closed", {"order_id": order.id, "outcome": "rejected"})
        return self.co.release_payout(order.id)

    # ------------------------------------------------------------------ background job
    def sweep(self, when: datetime | None = None) -> dict[str, list[str]]:
        """Timers: a silent seller approves, a return that never arrives lapses. Run before the payout sweep."""
        when = when or now()
        approved, lapsed, escalated = [], [], []
        for o in self.store.orders_in_status([OrderStatus.RETURN_REQUESTED]):
            if o.return_request and o.return_request.respond_by <= when:
                won: dict[str, bool] = {}

                def auto(x: Order) -> bool:
                    won.clear()
                    if x.status != OrderStatus.RETURN_REQUESTED or x.return_request is None or x.return_request.respond_by > when:
                        return False
                    self._approve(x, "auto", when)
                    won["yes"] = True
                    return True
                try:
                    updated = self.store.update_order(o.id, auto)
                    if won:
                        approved.append(o.id)
                        self._announce(updated, auto=True)
                except Exception:    # one broken order must not stop the timers of everyone else
                    log.exception("return timer failed for %s", o.id)
        for o in self.store.orders_in_status([OrderStatus.RETURN_APPROVED]):
            if o.return_request and o.return_request.lapse_at and o.return_request.lapse_at <= when:
                kind: dict[str, str] = {}

                def lapse(x: Order) -> bool:
                    kind.clear()
                    r = x.return_request
                    if x.status != OrderStatus.RETURN_APPROVED or r is None or not r.lapse_at or r.lapse_at > when:
                        return False
                    if r.return_waybill:    # the buyer says it is on its way: a person looks at it
                        self._dispute(x, "the item was reported sent back but the seller never confirmed it")
                        kind["k"] = "escalated"
                    else:
                        x.status = OrderStatus.DELIVERED
                        r.outcome, r.resolved_at = "lapsed", when
                        x.payout_due_at = when
                        x.log("the item never came back; the return lapsed and the payout is released")
                        kind["k"] = "lapsed"
                    return True
                try:
                    self.store.update_order(o.id, lapse)
                    if kind.get("k") == "lapsed":
                        lapsed.append(o.id)
                        self.events.publish("return.closed", {"order_id": o.id, "outcome": "lapsed"})
                    elif kind.get("k") == "escalated":
                        escalated.append(o.id)
                        self.events.publish("return.disputed", {"order_id": o.id})      # the seller is told too
                        self.events.publish("ops.manual_action", {"order_id": o.id, "action": "dispute"})
                except Exception:
                    log.exception("return timer failed for %s", o.id)
        return {"returns_auto_approved": approved, "returns_lapsed": lapsed, "returns_escalated": escalated}

    # ------------------------------------------------------------------ helpers
    def _approve(self, o: Order, by: str, at: datetime | None = None) -> None:
        r = o.return_request
        r.approved_by, r.approved_at = by, at or now()
        r.lapse_at = r.approved_at + timedelta(days=self.settings.return_lapse_days)
        o.status = OrderStatus.RETURN_APPROVED
        o.log("return approved by the seller" if by == "seller" else "return approved automatically: the seller did not answer in time")

    def _dispute(self, o: Order, why: str) -> None:
        o.status = OrderStatus.RETURN_DISPUTED
        o.log(why)

    def _announce(self, order: Order, auto: bool) -> None:
        if order.status == OrderStatus.RETURN_APPROVED:
            self.events.publish("return.approved", {"order_id": order.id, "auto": auto})
        elif order.status == OrderStatus.RETURN_DISPUTED:
            self.events.publish("return.disputed", {"order_id": order.id})
            self.events.publish("ops.manual_action", {"order_id": order.id, "action": "dispute"})

    def _refund_return(self, order_id: str, why: str, from_states: tuple[OrderStatus, ...]) -> Order:
        won: dict[str, int] = {}

        def claim(o: Order) -> bool:
            won.clear()
            if o.status not in from_states or o.return_request is None:
                return False
            r = o.return_request
            amount = o.subtotal if r.reason == ReturnReason.CHANGED_MIND else o.total
            o.status = OrderStatus.REFUNDED
            r.outcome, r.refund_amount, r.resolved_at = "refunded", amount, now()
            o.log(f"return completed: {why}")
            won["amount"] = amount
            return True
        order = self.store.update_order(order_id, claim)
        if order is None:
            raise CheckoutError("order not found")
        if not won:
            raise CheckoutError(f"order is {order.status.value}; nothing to refund here")
        order = self.co._refund(order, f"return: {order.return_request.reason.value}", won["amount"])   # money first
        if order.return_request.reason == ReturnReason.CHANGED_MIND:
            self.co._restock(order.id)      # a change-of-mind item is unused and goes back on the shelf; damaged ones are inspected first
        self.events.publish("return.refunded", {"order_id": order.id})
        return order

    def _seller_order(self, seller_id: str, order_id: str) -> Order:
        return self.co._seller_order(seller_id, order_id)
