# 6. Shipping

Code: `prototype/etalase/adapters/shipping.py`.

## Provider

Indonesia uses **Biteship**, an aggregator with one API for 30+ couriers (JNE, J&T, SiCepat,
AnterAja and others), including COD (which we do not use, see ADR 0009) and inter-island shipping. One integration instead of one per
courier. RajaOngkir is an alternative for rate checks.

| Call | When | Biteship endpoint |
|---|---|---|
| `get_rates` | Agent asks for shipping options or creates a checkout | `POST /v1/rates/couriers` |
| `book` | Seller accepts and has no tracking number yet | `POST /v1/orders` |
| `parse_webhook` | Courier status changes | Webhook to `/webhooks/shipping` |

Field names in the adapter follow Biteship's documentation and must be re-checked before going live.

## Status mapping

Each provider's statuses map onto our own, because **`delivered` starts the payout clock** (the money is released after the return window, see [doc 05](05-checkout-payment-payout.md#payout-hold-and-returns)) and `returned` triggers a refund:

| Internal | Meaning |
|---|---|
| `booked` | Shipment created |
| `picked_up` | Courier has the parcel (or the seller sent a tracking number) |
| `in_transit` | On the way |
| `delivered` | Delivered → `delivered_at` set, payout due after the hold (72 h, or 168 h for a new seller) |
| `returned` | Courier brought the parcel back to the seller → buyer refunded in full, stock restored, seller told |
| `failed` (Biteship `cancelled`, `courier_not_found` and similar) | One `ops.manual_action` alert (`undelivered_parcel`); the money stays frozen until an operator looks |

## Returns and return shipping

A buyer who returns an item sends it back with any courier and the agent may report the tracking number
(`submit_return_tracking`). Etalase does **not** book return shipments yet; prepaid return labels are on the roadmap.
In this version the **buyer pays the return shipping for every reason** and Etalase does not reimburse it; the policy shown to agents and on the
store's return page says so. Whether a defect return should be reimbursed (from the held payout, or with a prepaid label) is an open decision
(rules and options in [doc 05](05-checkout-payment-payout.md#payout-hold-and-returns)). The cost of a parcel that comes back by itself (`returned`)
is not assigned yet; today the buyer is refunded in full.

## Weight

Shipping cost depends on weight, which WhatsApp sellers rarely record. Etalase estimates it from the
product type (hijab ≈ 150 g, T-shirt ≈ 200 g, shoes ≈ 900 g), shows the estimate in the catalog
summary and lets the seller correct it.

## Domestic only

Delivery must stay inside the seller's country for now ([ADR 0001](adr/0001-domestic-first.md)).
A buyer abroad can still buy for someone in Indonesia, as long as the delivery address is in
Indonesia. Cross-border shipping (duties, HS codes, restricted goods) is roadmap phase 4.

## Cost notes

Rate checks cost a few rupiah each, so results for the same route and weight are worth caching for
a few minutes. The shipping fee is paid by the buyer and passed to the courier; it is kept separate
from the seller's payout.
