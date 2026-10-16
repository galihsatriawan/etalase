"""In-memory store for local runs, demos and tests.

Stock operations take one lock, which gives the same guarantee the Firestore
implementation gets from transactions: two buyers can never both take the last unit.
"""

from __future__ import annotations

import copy
import threading
from datetime import datetime
from typing import Callable

from ..models import Connection, Order, OrderStatus, OutOfStock, Product, Reservation, Seller, StockChange, now


class MemoryStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sellers: dict[str, Seller] = {}
        self._products: dict[str, Product] = {}
        self._orders: dict[str, Order] = {}
        self._reservations: dict[str, list[Reservation]] = {}
        self._stock_log: list[StockChange] = []
        self._connections: dict[str, Connection] = {}
        self._docs: dict[tuple[str, str], dict] = {}

    # ---- sellers
    def put_seller(self, seller: Seller) -> None:
        with self._lock:
            self._sellers[seller.id] = seller.model_copy(deep=True)

    def get_seller(self, seller_id: str) -> Seller | None:
        with self._lock:
            s = self._sellers.get(seller_id)
            return s.model_copy(deep=True) if s else None

    def find_seller_by_whatsapp(self, number: str) -> Seller | None:
        with self._lock:
            for s in self._sellers.values():
                if s.whatsapp == number:
                    return s.model_copy(deep=True)
        return None

    def find_seller_by_slug(self, slug: str) -> Seller | None:
        with self._lock:
            for s in self._sellers.values():
                if s.slug and s.slug == slug:
                    return s.model_copy(deep=True)
        return None

    def find_seller_by_address(self, address: str) -> Seller | None:
        if not address.startswith("tg:"):
            return self.find_seller_by_whatsapp(address)
        with self._lock:
            for s in self._sellers.values():
                if s.telegram_chat_id and s.telegram_chat_id == address[3:]:
                    return s.model_copy(deep=True)
        return None

    # ---- products
    def put_product(self, product: Product) -> None:
        with self._lock:
            product.updated_at = now()
            stored = product.model_copy(deep=True)
            old = self._products.get(product.id)
            if old:
                old_stock = {v.id: v.stock for v in old.variants}
                for v in stored.variants:
                    if v.id in old_stock:
                        v.stock = old_stock[v.id].model_copy()
            self._products[product.id] = stored

    def get_product(self, product_id: str) -> Product | None:
        with self._lock:
            p = self._products.get(product_id)
            return p.model_copy(deep=True) if p else None

    def list_products(self, seller_id: str | None = None, status: str | None = None) -> list[Product]:
        with self._lock:
            out = [p for p in self._products.values()
                   if (seller_id is None or p.seller_id == seller_id)
                   and (status is None or p.status == status)]
            return [p.model_copy(deep=True) for p in out]

    # ---- orders
    def put_order(self, order: Order) -> None:
        with self._lock:
            self._orders[order.id] = order.model_copy(deep=True)

    def get_order(self, order_id: str) -> Order | None:
        with self._lock:
            o = self._orders.get(order_id)
            return o.model_copy(deep=True) if o else None

    def find_order_by_payment_ref(self, reference: str) -> Order | None:
        with self._lock:
            for o in self._orders.values():
                if o.payment and o.payment.reference == reference:
                    return o.model_copy(deep=True)
        return None

    def list_orders(self, seller_id: str) -> list[Order]:
        with self._lock:
            return [o.model_copy(deep=True) for o in self._orders.values() if o.seller_id == seller_id]

    def orders_in_status(self, statuses: list[OrderStatus]) -> list[Order]:
        with self._lock:
            return [o.model_copy(deep=True) for o in self._orders.values() if o.status in statuses]

    def update_order(self, order_id: str, change: Callable[[Order], bool]) -> Order | None:
        with self._lock:
            current = self._orders.get(order_id)
            if current is None:
                return None
            working = current.model_copy(deep=True)
            if change(working):
                self._orders[order_id] = working.model_copy(deep=True)
            return working

    # ---- protocol documents
    def put_doc(self, kind: str, doc_id: str, data: dict) -> None:
        with self._lock:
            self._docs[(kind, doc_id)] = copy.deepcopy(data)

    def get_doc(self, kind: str, doc_id: str) -> dict | None:
        with self._lock:
            d = self._docs.get((kind, doc_id))
            return copy.deepcopy(d) if d is not None else None

    def create_doc(self, kind: str, doc_id: str, data: dict) -> bool:
        with self._lock:
            if (kind, doc_id) in self._docs:
                return False
            self._docs[(kind, doc_id)] = copy.deepcopy(data)
            return True

    def update_doc(self, kind: str, doc_id: str, change: Callable[[dict], bool]) -> dict | None:
        with self._lock:
            current = self._docs.get((kind, doc_id))
            if current is None:
                return None
            working = copy.deepcopy(current)
            if change(working):
                self._docs[(kind, doc_id)] = copy.deepcopy(working)
            return working

    # ---- stock
    def _variant(self, product_id: str, variant_id: str):
        product = self._products[product_id]
        return product, product.variant(variant_id)

    def reserve(self, order_id: str, product_id: str, variant_id: str, qty: int, expires_at: datetime) -> Reservation:
        if qty <= 0:
            raise ValueError("qty must be positive")
        with self._lock:
            _, v = self._variant(product_id, variant_id)
            avail = v.stock.available
            if avail is not None and avail < qty:
                raise OutOfStock(f"only {avail} left")
            v.stock.reserved += qty
            res = Reservation(order_id=order_id, product_id=product_id, variant_id=variant_id,
                              qty=qty, expires_at=expires_at)
            self._reservations.setdefault(order_id, []).append(res)
            return res.model_copy()

    def commit(self, order_id: str) -> None:
        """Paid: move held units out of stock. Idempotent."""
        with self._lock:
            for res in self._reservations.get(order_id, []):
                if res.status != "held":
                    continue
                _, v = self._variant(res.product_id, res.variant_id)
                v.stock.reserved = max(0, v.stock.reserved - res.qty)
                if v.stock.on_hand is not None:
                    v.stock.on_hand = max(0, v.stock.on_hand - res.qty)
                res.status = "committed"

    def release(self, order_id: str) -> None:
        """Expired or rejected before payment: give held units back. Idempotent."""
        with self._lock:
            for res in self._reservations.get(order_id, []):
                if res.status != "held":
                    continue
                _, v = self._variant(res.product_id, res.variant_id)
                v.stock.reserved = max(0, v.stock.reserved - res.qty)
                res.status = "released"

    def restock(self, order_id: str) -> None:
        """Refunded after payment: put committed units back on hand. Idempotent."""
        with self._lock:
            for res in self._reservations.get(order_id, []):
                if res.status != "committed":
                    continue
                _, v = self._variant(res.product_id, res.variant_id)
                if v.stock.on_hand is not None:
                    v.stock.on_hand += res.qty
                res.status = "restocked"

    def extend(self, order_id: str, expires_at: datetime) -> None:
        with self._lock:
            for res in self._reservations.get(order_id, []):
                if res.status == "held":
                    res.expires_at = expires_at

    def set_stock(self, seller_id: str, product_id: str, variant_id: str, on_hand: int | None, source: str) -> StockChange:
        with self._lock:
            product, v = self._variant(product_id, variant_id)
            if product.seller_id != seller_id:
                raise PermissionError("product belongs to another seller")
            change = StockChange(seller_id=seller_id, product_id=product_id, variant_id=variant_id,
                                 before=v.stock.on_hand, after=on_hand, source=source)
            v.stock.on_hand = on_hand
            v.stock.last_confirmed_at = now()
            v.stock.source = source
            self._stock_log.append(change)
            return change

    # ---- connections
    def put_connection(self, connection: Connection) -> None:
        with self._lock:
            self._connections[connection.id] = connection.model_copy()

    def find_connection_by_key_hash(self, key_hash: str) -> Connection | None:
        with self._lock:
            for c in self._connections.values():
                if c.api_key_hash == key_hash:
                    return c.model_copy()
        return None

    def list_connections(self, seller_id: str) -> list[Connection]:
        with self._lock:
            return [c.model_copy() for c in self._connections.values() if c.seller_id == seller_id]

    def held_reservations_expiring_before(self, when: datetime) -> list[Reservation]:
        with self._lock:
            return [r.model_copy() for rs in self._reservations.values() for r in rs
                    if r.status == "held" and r.expires_at <= when]

    def stock_log(self, seller_id: str) -> list[StockChange]:
        with self._lock:
            return [c for c in self._stock_log if c.seller_id == seller_id]
