# 8. Integrations: Sheets, OMS, e-commerce, website builders and marketplaces

Two integration styles:

| Style | Who moves the data | Used for |
|---|---|---|
| **Pull adapters** | Etalase reads the partner's API | Google Sheets, WooCommerce, Jubelio, Ginee, marketplace stock sync |
| **Push: Connector API** | The partner calls Etalase | Local website builders, small platforms, any OMS that prefers webhooks |

Whatever the style, the rule from [ADR 0002](adr/0002-single-stock-owner.md) holds: one stock owner
per seller.

## OMS vs e-commerce platform

- An **e-commerce platform** (WooCommerce, Shopify, a website builder) is where a seller *sells*: it
  has a storefront, catalog and checkout.
- An **OMS** (Jubelio, Ginee) is where a seller *operates*: it syncs stock and orders across
  Shopee, TikTok Shop, Tokopedia and websites.
- If a seller has both, Etalase connects to the OMS, so stock still has one owner.

## Connector API (OpenAPI)

Integrate once, enable every store on a platform. FastAPI publishes the spec at `/openapi.json`
(interactive docs at `/docs`).

| Endpoint | Purpose |
|---|---|
| `PUT /connector/v1/products/{external_id}` | Create or update a product with variants, price, weight, stock |
| `PATCH /connector/v1/stock` | Bulk stock updates by SKU |
| `POST /connector/v1/orders/{order_id}/status` | `accepted`, `rejected` or `shipped` (+ waybill) |
| Outgoing webhook `order.paid` | Paid orders pushed to the partner, signed with `X-Etalase-Signature` (HMAC-SHA256) |

Authentication: one API key per connection in `X-Etalase-Key`. Only its SHA-256 hash is stored and
the key is shown once.

```bash
curl -X PUT "$BASE/connector/v1/products/sku-arabica-250" \
  -H "X-Etalase-Key: $KEY" -H "content-type: application/json" \
  -d '{"title":"Arabica Coffee Beans 250 g","description":"Single-origin arabica from Malang, medium roast.",
       "category":"Food & Drink > Coffee","images":["https://example.com/arabica.jpg"],
       "variants":[{"external_id":"v1","sku":"ARA-250","options":{"grind":"Whole bean"},
                    "price":95000,"weight_gram":300,"stock":40}]}'
```

Verifying our webhook on the partner side:

```python
expected = hmac.new(webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
assert hmac.compare_digest(expected, request.headers["X-Etalase-Signature"])
```

## Instagram (optional catalog source, in the MVP)

Instagram is where many sellers' products already live as **photo + caption**. WhatsApp stays the
place where the seller works; Instagram is only a catalog source, and connecting it is optional.

1. The seller taps "Connect Instagram" in the dashboard and signs in with **Instagram Login** (OAuth).
   A professional (Business or Creator) account is required.
2. Etalase reads recent posts through the **Instagram Graph API** (`/me/media`: image, caption,
   permalink; carousel children for albums; videos skipped for now).
3. Gemini reads each photo and caption, creates drafts (name, variants, price, estimated weight) and
   skips non-product posts (promos, thank-you posts).
4. The summary goes to the seller's **WhatsApp** with Publish / Edit, like any other catalog input.
   Imported posts are remembered (`external_ref = ig:<post id>`), so re-imports only add new posts.
5. Instagram has no stock, so variants without a count stay in confirm-first mode until the seller
   sets stock by chat.

Scope: `instagram_business_basic` only. Public launch needs Meta App Review; development mode works
with test users, which is enough for the hackathon. Buyer DMs are out of scope.

Prototype: `adapters/instagram.py` (`InstagramGraphSource`, `SampleInstagramSource`),
`GET /connect/instagram` → `GET /connect/instagram/callback`, and `POST /dev/instagram/import` with
`samples/instagram_posts.json` for offline demos.

## Google Sheets (designed, phase 1 follow-up)

- **OAuth + Google Picker with the `drive.file` scope:** the seller signs in with their Google account
  and picks the sheet; Etalase can read only the files the seller chose.
- Gemini maps the columns ("column B = product name, D = colour, F = stock — correct?"); the seller
  confirms in WhatsApp.
- Changes are detected by checking the file's `modifiedTime` every few minutes (or Drive change
  notifications) and diffing rows.
- Refresh tokens are encrypted (Secret Manager / Cloud KMS) per seller; a revoked token triggers a
  "reconnect" message.
- For the hackathon, a service-account share or OAuth in testing mode is enough.

## E-commerce plugins (roadmap phase 3)

**WooCommerce first**, because:

- It is the seller's own store: they can install a plugin without platform approval, and its REST API
  is open.
- Many Indonesian WooCommerce stores use local payments (Xendit, Midtrans) and are not reached by
  Stripe's agentic commerce suite.
- Shopify already ships agentic storefronts and several agent-readiness apps.

This is an assumption to validate in seller interviews; if most website sellers use a local builder,
that builder comes first through the Connector API.

## Marketplaces (Shopee, TikTok Shop)

Marketplaces offer partner programmes (Shopee Open Platform, TikTok Shop Partner Center, which now
also serves Tokopedia). Their APIs are meant for managing the seller's shop on that marketplace, so
Etalase uses them for **stock and order sync only**: when something sells through an AI agent,
stock drops on Shopee too, and the other way round. Etalase does **not** resell marketplace
listings to agents; that would compete with the marketplaces' own agent partnerships and likely
breach their partner terms. Partner app stores are also a distribution channel for Etalase.

## OMS adapters (roadmap phase 3)

Jubelio and Ginee: read catalog and stock, send agent orders into the OMS so it updates every
channel. Requires each OMS's partner programme; terms to be confirmed.
