"""Domain models shared by every service and adapter."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class StockOwner(str, Enum):
    INTERNAL = "internal"  # direct sellers: Etalase owns stock (chat, Sheets, orders update it)
    OMS = "oms"            # OMS sellers: the OMS owns stock, Etalase only reads it
    STORE = "store"        # e-commerce / website-builder sellers: their store owns stock (Connector API)


class SellMode(str, Enum):
    INSTANT = "instant"              # buyer can pay right away
    CONFIRM_FIRST = "confirm_first"  # seller confirms stock before the payment link is sent


class ProductStatus(str, Enum):
    DRAFT = "draft"
    NEEDS_REVIEW = "needs_review"
    PUBLISHED = "published"


class OrderStatus(str, Enum):
    AWAITING_SELLER_CONFIRMATION = "awaiting_seller_confirmation"
    AWAITING_PAYMENT = "awaiting_payment"
    PAID = "paid"                # paid, waiting for the seller to accept (instant mode)
    ACCEPTED = "accepted"        # seller will ship
    REJECTED = "rejected"
    EXPIRED = "expired"
    REFUNDED = "refunded"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    PAID_OUT = "paid_out"


class Address(BaseModel):
    name: str
    phone: str = ""
    line1: str = ""
    city: str = ""
    postal_code: str = ""
    country: str = "ID"


class Seller(BaseModel):
    id: str = Field(default_factory=lambda: new_id("sel"))
    name: str
    whatsapp: str                 # E.164 without "+", e.g. 6281234567890
    country: str = "ID"
    stock_owner: StockOwner = StockOwner.INTERNAL
    origin: Address | None = None
    kyc_status: str = "pending"   # verified by the payment provider, never stored here
    payout_account_id: str | None = None  # payment-provider sub-account id
    commission_rate: float | None = None


class Stock(BaseModel):
    on_hand: int | None = None    # None = seller does not track stock
    reserved: int = 0
    buffer: int = 0
    last_confirmed_at: datetime | None = None
    source: str = "chat"

    @property
    def available(self) -> int | None:
        if self.on_hand is None:
            return None
        return max(0, self.on_hand - self.reserved - self.buffer)


class Variant(BaseModel):
    id: str = Field(default_factory=lambda: new_id("var"))
    sku: str
    external_ref: str | None = None  # id in the seller's OMS / store
    options: dict[str, str] = Field(default_factory=dict)  # {"color": "Black"}
    price: int                    # minor units of the country currency (IDR has none)
    weight_gram: int = 0
    stock: Stock = Field(default_factory=Stock)

    @property
    def label(self) -> str:
        return " / ".join(self.options.values()) or "Default"


class Product(BaseModel):
    id: str = Field(default_factory=lambda: new_id("prd"))
    seller_id: str
    external_ref: str | None = None  # id in the seller's OMS / store
    title: str
    description: str = ""
    category: str = ""
    images: list[str] = Field(default_factory=list)
    attributes: dict[str, str] = Field(default_factory=dict)
    ships_to: list[str] = Field(default_factory=lambda: ["ID"])
    variants: list[Variant] = Field(default_factory=list)
    status: ProductStatus = ProductStatus.DRAFT
    readiness_score: int = 0
    readiness_missing: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=now)

    def variant(self, variant_id: str) -> Variant:
        for v in self.variants:
            if v.id == variant_id:
                return v
        raise KeyError(variant_id)


class Reservation(BaseModel):
    id: str = Field(default_factory=lambda: new_id("rsv"))
    order_id: str
    product_id: str
    variant_id: str
    qty: int
    status: str = "held"          # held -> committed | released
    expires_at: datetime


class ShippingRate(BaseModel):
    courier: str
    service: str
    price: int
    etd: str = ""


class PaymentInfo(BaseModel):
    provider: str
    reference: str
    method: str = "qris"
    qr_string: str = ""
    provider_id: str = ""         # the provider's own id (Xendit qr_id), needed for refunds and test-mode simulation
    status: str = "pending"       # pending -> paid -> refunded


class Shipment(BaseModel):
    courier: str
    service: str
    waybill: str = ""             # may be empty until the courier issues it (provider bookings)
    status: str = "booked"        # booked -> picked_up -> in_transit -> delivered; or returned / failed
    provider_order_id: str = ""   # the aggregator's own order id (Biteship `id`); webhooks identify the order by it
    tracking_id: str = ""         # the aggregator's tracking id (Biteship `courier.tracking_id`)


class Payout(BaseModel):
    seller_amount: int
    commission: int
    status: str = "scheduled"     # scheduled -> released


class OrderItem(BaseModel):
    product_id: str
    variant_id: str
    sku: str
    title: str
    qty: int
    unit_price: int
    weight_gram: int = 0          # per unit, for courier bookings


class Order(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ord"))
    seller_id: str
    items: list[OrderItem]
    buyer: Address
    sell_mode: SellMode
    status: OrderStatus
    currency: str = "IDR"
    subtotal: int
    shipping_rate: ShippingRate | None = None
    shipping_fee: int = 0
    total: int
    payment: PaymentInfo | None = None
    shipment: Shipment | None = None
    payout: Payout | None = None
    seller_confirmed: bool = False
    created_at: datetime = Field(default_factory=now)
    expires_at: datetime | None = None
    history: list[str] = Field(default_factory=list)

    def log(self, message: str) -> None:
        self.history.append(f"{now().isoformat(timespec='seconds')} {message}")


class StockChange(BaseModel):
    id: str = Field(default_factory=lambda: new_id("stk"))
    seller_id: str
    product_id: str
    variant_id: str
    before: int | None
    after: int | None
    source: str
    at: datetime = Field(default_factory=now)


class Connection(BaseModel):
    """A connected external system (OMS, e-commerce plugin, website builder via the Connector API)."""
    id: str = Field(default_factory=lambda: new_id("con"))
    seller_id: str
    kind: str = "connector_api"         # connector_api | woocommerce | jubelio | ginee | google_sheets
    api_key_hash: str = ""              # sha256 of the key; the key itself is shown once
    order_webhook_url: str | None = None
    webhook_secret: str = ""
    # OAuth token for pull adapters (Instagram, Google Sheets). Prototype keeps it here;
    # production stores it encrypted with Cloud KMS / Secret Manager.
    access_token: str = ""
    created_at: datetime = Field(default_factory=now)


class OutOfStock(Exception):
    pass


class CheckoutError(Exception):
    pass
