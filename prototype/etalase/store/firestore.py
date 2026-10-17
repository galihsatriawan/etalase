"""Firestore store (production path).

Stock changes run inside Firestore transactions, so concurrent checkouts on the last
unit are serialised by the database. Run against the emulator before using real data:

    gcloud emulators firestore start --host-port=localhost:8681
    export FIRESTORE_EMULATOR_HOST=localhost:8681 STORE_BACKEND=firestore

Note: this module is exercised manually against the emulator; the automated tests use
MemoryStore, which implements the same contract.
"""

from __future__ import annotations

from datetime import datetime

from ..models import Connection, Order, OrderStatus, OutOfStock, Product, Reservation, Seller, StockChange, now

try:
    from google.cloud import firestore  # type: ignore
except ImportError:  # pragma: no cover
    firestore = None


class FirestoreStore:
    def __init__(self, project: str | None = None) -> None:
        if firestore is None:
            raise RuntimeError("pip install google-cloud-firestore to use STORE_BACKEND=firestore")
        self.db = firestore.Client(project=project)

    # ---- helpers
    def _col(self, name: str):
        return self.db.collection(name)

    @staticmethod
    def _dump(model) -> dict:
        return model.model_dump(mode="json")

    # ---- sellers
    def put_seller(self, seller: Seller) -> None:
        self._col("sellers").document(seller.id).set(self._dump(seller))

    def get_seller(self, seller_id: str) -> Seller | None:
        snap = self._col("sellers").document(seller_id).get()
        return Seller.model_validate(snap.to_dict()) if snap.exists else None

    def find_seller_by_whatsapp(self, number: str) -> Seller | None:
        for snap in self._col("sellers").where("whatsapp", "==", number).limit(1).stream():
            return Seller.model_validate(snap.to_dict())
        return None

    def pending_sellers(self) -> list[Seller]:
        return [Seller.model_validate(snap.to_dict()) for snap in self._col("sellers").where("kyc_status", "!=", "verified").stream()]

    def find_seller_by_slug(self, slug: str) -> Seller | None:
        for snap in self._col("sellers").where("slug", "==", slug).limit(1).stream():
            return Seller.model_validate(snap.to_dict())
        return None

    def find_seller_by_address(self, address: str) -> Seller | None:
        if not address.startswith("tg:"):
            return self.find_seller_by_whatsapp(address)
        for snap in self._col("sellers").where("telegram_chat_id", "==", address[3:]).limit(1).stream():
            return Seller.model_validate(snap.to_dict())
        return None

    # ---- products
    def put_product(self, product: Product) -> None:
        product.updated_at = now()
        ref = self._col("products").document(product.id)

        @firestore.transactional
        def txn(transaction):
            snap = ref.get(transaction=transaction)
            data = product.model_copy(deep=True)
            if snap.exists:  # keep stock owned by the reservation functions
                old = {v.id: v.stock for v in Product.model_validate(snap.to_dict()).variants}
                for v in data.variants:
                    if v.id in old:
                        v.stock = old[v.id]
            transaction.set(ref, self._dump(data))

        txn(self.db.transaction())

    def get_product(self, product_id: str) -> Product | None:
        snap = self._col("products").document(product_id).get()
        return Product.model_validate(snap.to_dict()) if snap.exists else None

    def list_products(self, seller_id: str | None = None, status: str | None = None) -> list[Product]:
        q = self._col("products")
        if seller_id:
            q = q.where("seller_id", "==", seller_id)
        if status:
            q = q.where("status", "==", status)
        return [Product.model_validate(s.to_dict()) for s in q.stream()]

    # ---- orders
    def put_order(self, order: Order) -> None:
        self._col("orders").document(order.id).set(self._dump(order))

    def get_order(self, order_id: str) -> Order | None:
        snap = self._col("orders").document(order_id).get()
        return Order.model_validate(snap.to_dict()) if snap.exists else None

    def find_order_by_payment_ref(self, reference: str) -> Order | None:
        for snap in self._col("orders").where("payment.reference", "==", reference).limit(1).stream():
            return Order.model_validate(snap.to_dict())
        return None

    def list_orders(self, seller_id: str) -> list[Order]:
        q = self._col("orders").where("seller_id", "==", seller_id)
        return [Order.model_validate(s.to_dict()) for s in q.stream()]

    def orders_in_status(self, statuses) -> list[Order]:
        q = self._col("orders").where("status", "in", [OrderStatus(x).value for x in statuses])
        return [Order.model_validate(s.to_dict()) for s in q.stream()]

    # ---- stock (transactional)
    def reserve(self, order_id: str, product_id: str, variant_id: str, qty: int, expires_at: datetime) -> Reservation:
        product_ref = self._col("products").document(product_id)
        res = Reservation(order_id=order_id, product_id=product_id, variant_id=variant_id, qty=qty, expires_at=expires_at)
        res_ref = self._col("reservations").document(res.id)

        @firestore.transactional
        def txn(transaction):
            product = Product.model_validate(product_ref.get(transaction=transaction).to_dict())
            v = product.variant(variant_id)
            avail = v.stock.available
            if avail is not None and avail < qty:
                raise OutOfStock(f"only {avail} left")
            v.stock.reserved += qty
            transaction.set(product_ref, self._dump(product))
            transaction.set(res_ref, {**self._dump(res), "expires_ts": res.expires_at})

        txn(self.db.transaction())
        return res

    def _apply(self, order_id: str, from_status: str, to_status: str, mutate) -> None:
        q = self._col("reservations").where("order_id", "==", order_id).where("status", "==", from_status)

        @firestore.transactional
        def txn(transaction):
            snaps = list(transaction.get(q))
            products: dict[str, Product] = {}
            for s in snaps:
                r = Reservation.model_validate(s.to_dict())
                if r.product_id not in products:
                    ref = self._col("products").document(r.product_id)
                    products[r.product_id] = Product.model_validate(ref.get(transaction=transaction).to_dict())
                mutate(products[r.product_id].variant(r.variant_id), r.qty)
                transaction.update(s.reference, {"status": to_status})
            for pid, p in products.items():
                transaction.set(self._col("products").document(pid), self._dump(p))

        txn(self.db.transaction())

    def update_order(self, order_id: str, change) -> Order | None:
        ref = self._col("orders").document(order_id)
        holder: dict[str, Order] = {}

        @firestore.transactional
        def txn(transaction):
            snap = ref.get(transaction=transaction)
            if not snap.exists:
                return
            order = Order.model_validate(snap.to_dict())
            if change(order):
                transaction.set(ref, self._dump(order))
            holder["o"] = order

        txn(self.db.transaction())
        return holder.get("o")

    # ---- protocol documents (one collection per kind)
    def put_doc(self, kind: str, doc_id: str, data: dict) -> None:
        self._col(f"doc_{kind}").document(doc_id).set(data)

    def get_doc(self, kind: str, doc_id: str) -> dict | None:
        snap = self._col(f"doc_{kind}").document(doc_id).get()
        return snap.to_dict() if snap.exists else None

    def create_doc(self, kind: str, doc_id: str, data: dict) -> bool:
        from google.api_core.exceptions import AlreadyExists   # a Firestore dependency, so present whenever this store is
        try:
            self._col(f"doc_{kind}").document(doc_id).create(data)    # fails if the document exists: one winner
        except AlreadyExists:
            return False
        return True

    def update_doc(self, kind: str, doc_id: str, change) -> dict | None:
        ref = self._col(f"doc_{kind}").document(doc_id)
        holder: dict[str, dict] = {}

        @firestore.transactional
        def txn(transaction):
            snap = ref.get(transaction=transaction)
            if not snap.exists:
                return
            data = snap.to_dict()
            if change(data):
                transaction.set(ref, data)
            holder["d"] = data

        txn(self.db.transaction())
        return holder.get("d")

    def extend(self, order_id: str, expires_at: datetime) -> None:
        q = self._col("reservations").where("order_id", "==", order_id).where("status", "==", "held")
        for snap in q.stream():
            snap.reference.update({"expires_at": expires_at.isoformat(), "expires_ts": expires_at})

    def commit(self, order_id: str) -> None:
        def m(v, qty):
            v.stock.reserved = max(0, v.stock.reserved - qty)
            if v.stock.on_hand is not None:
                v.stock.on_hand = max(0, v.stock.on_hand - qty)
        self._apply(order_id, "held", "committed", m)

    def release(self, order_id: str) -> None:
        def m(v, qty):
            v.stock.reserved = max(0, v.stock.reserved - qty)
        self._apply(order_id, "held", "released", m)

    def restock(self, order_id: str) -> None:
        def m(v, qty):
            if v.stock.on_hand is not None:
                v.stock.on_hand += qty
        self._apply(order_id, "committed", "restocked", m)

    def set_stock(self, seller_id: str, product_id: str, variant_id: str, on_hand: int | None, source: str) -> StockChange:
        ref = self._col("products").document(product_id)
        holder: dict[str, StockChange] = {}

        @firestore.transactional
        def txn(transaction):
            product = Product.model_validate(ref.get(transaction=transaction).to_dict())
            if product.seller_id != seller_id:
                raise PermissionError("product belongs to another seller")
            v = product.variant(variant_id)
            change = StockChange(seller_id=seller_id, product_id=product_id, variant_id=variant_id,
                                 before=v.stock.on_hand, after=on_hand, source=source)
            v.stock.on_hand, v.stock.last_confirmed_at, v.stock.source = on_hand, now(), source
            transaction.set(ref, self._dump(product))
            transaction.set(self._col("stock_log").document(change.id), self._dump(change))
            holder["c"] = change

        txn(self.db.transaction())
        return holder["c"]

    # ---- connections
    def put_connection(self, connection: Connection) -> None:
        self._col("connections").document(connection.id).set(self._dump(connection))

    def find_connection_by_key_hash(self, key_hash: str) -> Connection | None:
        for snap in self._col("connections").where("api_key_hash", "==", key_hash).limit(1).stream():
            return Connection.model_validate(snap.to_dict())
        return None

    def list_connections(self, seller_id: str) -> list[Connection]:
        q = self._col("connections").where("seller_id", "==", seller_id)
        return [Connection.model_validate(s.to_dict()) for s in q.stream()]

    def held_reservations_expiring_before(self, when: datetime) -> list[Reservation]:
        # needs a composite index on (status, expires_ts)
        q = self._col("reservations").where("status", "==", "held").where("expires_ts", "<=", when)
        return [Reservation.model_validate(s.to_dict()) for s in q.stream()]

    def stock_log(self, seller_id: str) -> list[StockChange]:
        q = self._col("stock_log").where("seller_id", "==", seller_id)
        return [StockChange.model_validate(s.to_dict()) for s in q.stream()]
