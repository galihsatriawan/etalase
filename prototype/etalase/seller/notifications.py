"""Turn order events into WhatsApp messages for the seller (subscribed to the event bus)."""

from __future__ import annotations

from typing import TYPE_CHECKING

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
        wa.send_buttons(seller.whatsapp, t("stock_check", items=items(order), city=order.buyer.city),
                        [(f"confirm:{order.id}", t("btn_available")), (f"oos:{order.id}", t("btn_sold_out"))])

    def on_paid(p):
        order, seller = order_and_seller(p)
        if order.status == OrderStatus.PAID:
            wa.send_buttons(seller.whatsapp,
                            t("new_paid_order", items=items(order), subtotal=rupiah(order.subtotal),
                              city=order.buyer.city, courier=courier(order)),
                            [(f"accept:{order.id}", t("btn_accept")), (f"oos:{order.id}", t("btn_out_of_stock"))])
        else:
            wa.send_text(seller.whatsapp, t("paid_ship_now", items=items(order), address=address(order),
                                            courier=courier(order)))

    def on_accepted(p):
        order, seller = order_and_seller(p)
        wa.send_text(seller.whatsapp, t("accepted", address=address(order)))

    def on_rejected(p):
        order, seller = order_and_seller(p)
        extra = t("refunded_extra") if p.get("paid") else ""
        wa.send_text(seller.whatsapp, t("cancelled", items=items(order), extra=extra))

    def on_payout(p):
        order, seller = order_and_seller(p)
        wa.send_text(seller.whatsapp, t("payout", amount=rupiah(order.payout.seller_amount),
                                        commission=rupiah(order.payout.commission)))

    bus.subscribe("order.awaiting_confirmation", on_awaiting_confirmation)
    bus.subscribe("order.paid", on_paid)
    bus.subscribe("order.accepted", on_accepted)
    bus.subscribe("order.rejected", on_rejected)
    bus.subscribe("payout.released", on_payout)
