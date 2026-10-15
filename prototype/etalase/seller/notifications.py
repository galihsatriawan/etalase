"""Turn order events into messages for the seller on their chosen channel (subscribed to the event bus)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..adapters.messaging import Template
from ..models import OrderStatus, now
from .format import defang, rupiah

if TYPE_CHECKING:
    from ..container import AppContext

log = logging.getLogger("etalase.notifications")


def register(ctx: "AppContext") -> None:
    bus, store, wa = ctx.events, ctx.store, ctx.messenger

    def order_and_seller(payload):
        order = store.get_order(payload["order_id"])
        return order, store.get_seller(order.seller_id)

    def items(order) -> str:
        return ", ".join(f"{i.title} × {i.qty}" for i in order.items)

    t = ctx.t

    def address(order) -> str:
        b = order.buyer
        return defang(f"{b.name}, {b.line1}, {b.city} {b.postal_code}")

    def courier(order) -> str:
        return f"{order.shipping_rate.courier.upper()} {order.shipping_rate.service}"

    def on_awaiting_confirmation(p):
        order, seller = order_and_seller(p)
        # Etalase starts these messages, so outside WhatsApp's 24-hour window they go out as approved templates.
        wa.send_notification(seller.address, t("stock_check", items=items(order), city=defang(order.buyer.city)),
                             [(f"confirm:{order.id}", t("btn_available")), (f"oos:{order.id}", t("btn_sold_out"))],
                             Template("stock_check", [items(order), defang(order.buyer.city)],
                                      [f"confirm:{order.id}", f"oos:{order.id}"]))

    def on_paid(p):
        order, seller = order_and_seller(p)
        if order.status == OrderStatus.PAID:
            wa.send_notification(seller.address,
                                 t("new_paid_order", items=items(order), subtotal=rupiah(order.subtotal),
                                   city=defang(order.buyer.city), courier=courier(order)),
                                 [(f"accept:{order.id}", t("btn_accept")), (f"oos:{order.id}", t("btn_out_of_stock"))],
                                 Template("new_paid_order", [items(order), rupiah(order.subtotal), defang(order.buyer.city), courier(order)],
                                          [f"accept:{order.id}", f"oos:{order.id}"]))
        else:
            wa.send_notification(seller.address, t("paid_ship_now", items=items(order), address=address(order),
                                                    courier=courier(order)),
                                 template=Template("paid_ship_now", [items(order), address(order), courier(order)]))

    def on_accepted(p):
        order, seller = order_and_seller(p)
        wa.send_text(seller.address, t("accepted", address=address(order)))

    def on_rejected(p):
        order, seller = order_and_seller(p)
        extra = t("refunded_extra") if p.get("paid") else ""
        wa.send_text(seller.address, t("cancelled", items=items(order), extra=extra))

    def on_payout(p):
        order, seller = order_and_seller(p)
        amount, commission = rupiah(order.payout.seller_amount), rupiah(order.payout.commission)
        wa.send_notification(seller.address, t("payout", amount=amount, commission=commission),
                             template=Template("payout", [amount, commission]))

    def best_effort(order, seller, key, **fields):
        """Information the seller can also read on the dashboard. It has no template, so outside WhatsApp's 24-hour
        window it is simply not sent; that must not show up as an error."""
        try:
            wa.send_notification(seller.address, t(key, items=items(order), **fields))
        except Exception as e:
            log.info("could not send %s to seller %s: %s", key, seller.id, e)

    def on_delivered(p):
        order, seller = order_and_seller(p)
        if order.payout_due_at and order.payout_due_at > now():   # no hold: the payout message follows at once
            rate = seller.commission_rate if seller.commission_rate is not None else ctx.settings.commission_rate
            amount = rupiah(order.subtotal - round(order.subtotal * rate))
            best_effort(order, seller, "delivered_hold", amount=amount, date=order.payout_due_at.strftime("%d %b %Y"))

    def on_returned_to_sender(p):
        order, seller = order_and_seller(p)
        best_effort(order, seller, "parcel_returned")

    def on_parcel_lost(p):
        order, seller = order_and_seller(p)
        best_effort(order, seller, "parcel_lost")

    def on_return_requested(p):
        order, seller = order_and_seller(p)
        r = order.return_request
        reason, note = t(f"reason_{r.reason.value}"), defang(r.note) or t("no_note")
        if r.evidence:     # the links themselves stay on the dashboard: they come from the buyer and are never pushed into chat
            # (links typed into the note are replaced too, see defang)
            note = f"{note} {t('return_photos', n=len(r.evidence))}"
        hours = ctx.settings.return_seller_response_hours
        wa.send_notification(seller.address, t("return_requested", items=items(order), reason=reason, note=note, hours=hours),
                             [(f"return_ok:{order.id}", t("btn_return_ok")), (f"return_no:{order.id}", t("btn_return_dispute"))],
                             Template("return_requested", [items(order), reason, note, str(hours)],
                                      [f"return_ok:{order.id}", f"return_no:{order.id}"]))

    def on_return_approved(p):
        order, seller = order_and_seller(p)
        days = ctx.settings.return_lapse_days
        wa.send_notification(seller.address, t("return_approved", items=items(order), days=days),
                             [(f"return_got:{order.id}", t("btn_return_got"))],
                             Template("return_approved", [items(order), str(days)], [f"return_got:{order.id}"]))

    def on_return_disputed(p):
        order, seller = order_and_seller(p)
        best_effort(order, seller, "return_disputed")

    def on_return_shipped_back(p):
        order, seller = order_and_seller(p)
        best_effort(order, seller, "return_shipped_back", waybill=order.return_request.return_waybill)

    def on_return_refunded(p):
        order, seller = order_and_seller(p)
        restock = t("restock_extra") if order.return_request.reason.value == "changed_mind" else ""
        best_effort(order, seller, "return_refunded", amount=rupiah(order.return_request.refund_amount), restock=restock)

    bus.subscribe("order.awaiting_confirmation", on_awaiting_confirmation)
    bus.subscribe("order.paid", on_paid)
    bus.subscribe("order.accepted", on_accepted)
    bus.subscribe("order.rejected", on_rejected)
    bus.subscribe("payout.released", on_payout)
    bus.subscribe("order.delivered", on_delivered)
    bus.subscribe("order.returned_to_sender", on_returned_to_sender)
    bus.subscribe("order.parcel_lost", on_parcel_lost)
    bus.subscribe("return.requested", on_return_requested)
    bus.subscribe("return.approved", on_return_approved)
    bus.subscribe("return.disputed", on_return_disputed)
    bus.subscribe("return.shipped_back", on_return_shipped_back)
    bus.subscribe("return.refunded", on_return_refunded)
