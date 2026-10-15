"""Turn order events into WhatsApp messages for the seller (subscribed to the event bus)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..adapters.messaging import Template
from ..models import OrderStatus
from .format import rupiah

if TYPE_CHECKING:
    from ..container import AppContext


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
        return f"{b.name}, {b.line1}, {b.city} {b.postal_code}"

    def courier(order) -> str:
        return f"{order.shipping_rate.courier.upper()} {order.shipping_rate.service}"

    def on_awaiting_confirmation(p):
        order, seller = order_and_seller(p)
        # Etalase starts these messages, so outside WhatsApp's 24-hour window they go out as approved templates.
        wa.send_notification(seller.whatsapp, t("stock_check", items=items(order), city=order.buyer.city),
                             [(f"confirm:{order.id}", t("btn_available")), (f"oos:{order.id}", t("btn_sold_out"))],
                             Template("stock_check", [items(order), order.buyer.city],
                                      [f"confirm:{order.id}", f"oos:{order.id}"]))

    def on_paid(p):
        order, seller = order_and_seller(p)
        if order.status == OrderStatus.PAID:
            wa.send_notification(seller.whatsapp,
                                 t("new_paid_order", items=items(order), subtotal=rupiah(order.subtotal),
                                   city=order.buyer.city, courier=courier(order)),
                                 [(f"accept:{order.id}", t("btn_accept")), (f"oos:{order.id}", t("btn_out_of_stock"))],
                                 Template("new_paid_order", [items(order), rupiah(order.subtotal), order.buyer.city, courier(order)],
                                          [f"accept:{order.id}", f"oos:{order.id}"]))
        else:
            wa.send_notification(seller.whatsapp, t("paid_ship_now", items=items(order), address=address(order),
                                                    courier=courier(order)),
                                 template=Template("paid_ship_now", [items(order), address(order), courier(order)]))

    def on_accepted(p):
        order, seller = order_and_seller(p)
        wa.send_text(seller.whatsapp, t("accepted", address=address(order)))

    def on_rejected(p):
        order, seller = order_and_seller(p)
        extra = t("refunded_extra") if p.get("paid") else ""
        wa.send_text(seller.whatsapp, t("cancelled", items=items(order), extra=extra))

    def on_payout(p):
        order, seller = order_and_seller(p)
        amount, commission = rupiah(order.payout.seller_amount), rupiah(order.payout.commission)
        wa.send_notification(seller.whatsapp, t("payout", amount=amount, commission=commission),
                             template=Template("payout", [amount, commission]))

    bus.subscribe("order.awaiting_confirmation", on_awaiting_confirmation)
    bus.subscribe("order.paid", on_paid)
    bus.subscribe("order.accepted", on_accepted)
    bus.subscribe("order.rejected", on_rejected)
    bus.subscribe("payout.released", on_payout)
