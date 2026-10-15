# 4. Catalog and stock

The catalog service is the single source of truth for what is sold and what is available.
Code: `prototype/etalase/catalog/`.

## Building a catalog from what sellers already have

```
WhatsApp photos / voice note / text / Excel
        │
        ▼
  Extraction ── Gemini multimodal, structured output (JSON schema)   ← GeminiExtractor
        │        offline rules for text and CSV (no credentials)      ← HeuristicExtractor
        ▼
  Normalisation ── colours (hitam → Black), titles, SKUs, category, default weights
        ▼
  Readiness score ── 0–100, list of what is missing
        ▼
  Draft → seller taps Publish → visible to agents
```

**Gemini extraction** (`extractor.py`) sends photos, voice notes and text in one request with
`response_schema=ExtractedCatalog`, so the output is always valid JSON with products, variants,
prices, estimated weights and follow-up questions. The prompt forbids inventing facts that are
not in the photos or the seller's words.

**Offline extraction** handles messages like `Voal hijab, black 5, navy 3, mocca 10, price 85k`
(or `Hijab voal, hitam 5, navy 3, harga 85rb`) and spreadsheets with Indonesian or English
headers. It keeps the demo and the tests independent of any API key.

**Media grouping:** photos sent within 15 minutes are attached to the next description, because
sellers usually send several photos and then explain them.

## Readiness score

| Check | Points |
|---|---|
| Title | 15 |
| Description (30+ characters) | 15 |
| Price on every variant | 20 |
| Shipping weight | 10 |
| Photo | 15 |
| Stock tracked | 15 |
| Category | 10 |

A product can be published once it has a title and a price; the score tells the seller what
would make agents more likely to pick it.

## Stock ownership

Each seller has **one** stock owner (see [ADR 0002](adr/0002-single-stock-owner.md)):

| Seller type | Stock owner | Updated by |
|---|---|---|
| WhatsApp / Instagram seller | Etalase (`internal`) | Chat, Google Sheets, orders |
| Seller with an OMS | The OMS (`oms`) | OMS via Connector API; chat updates are refused |
| Website / e-commerce seller | Their store (`store`) | Plugin or builder via Connector API |

## Reservations

Stock that can be sold is computed, never stored: `available = on_hand − reserved − buffer`.

| Event | Effect | Idempotent |
|---|---|---|
| `create_checkout` | `reserve`: fails if `available < qty` | – |
| Payment confirmed | `commit`: `on_hand −= qty`, `reserved −= qty` | ✅ |
| Unpaid after 15 min, or seller says sold out | `release`: `reserved −= qty` | ✅ |
| Refund after payment | `restock`: `on_hand += qty` | ✅ |

`MemoryStore` serialises these with a lock; `FirestoreStore` uses Firestore transactions. The test
`test_last_unit_cannot_be_sold_twice_under_concurrency` fires 100 concurrent reservations at the
last unit: exactly one succeeds.

Expiry runs every minute in the prototype. In production, one Cloud Task per order fires at
`expires_at` (Firestore TTL is not used because TTL deletion is not immediate).

## Sell mode

Decided per variant, every time an agent asks:

```python
if seller.stock_owner != "internal":   return INSTANT        # synced from OMS / store
if on_hand is None:                     return CONFIRM_FIRST  # seller does not track stock
if last confirmed > 3 days ago:         return CONFIRM_FIRST  # stale
if available <= 2:                      return CONFIRM_FIRST  # nearly sold out
return INSTANT
```

Agents see this as `availability: in_stock | confirm_with_seller`; sold-out variants are hidden.

## Updates by chat

| Seller says | Result |
|---|---|
| `navy sold out` / `navy habis` | Navy → 0 |
| `navy 2 left` / `navy tinggal 2` | Navy → 2 |
| `restock mocca 20` | Mocca + 20 |
| `voal hijab price 90k` / `harga hijab voal jadi 90rb` | Price → 90,000 |

With Gemini configured, the ADK seller agent interprets free text and asks when something is
ambiguous; without it, the offline intent parser handles these patterns. Every change is written to
a stock log (who, when, source, before, after) and published as `stock.changed`.

## Search for agents

`search_products` matches query words against title, category, description and attributes,
understands colour words in both languages, filters by price and destination country and only
returns published, in-stock (or confirm-first) variants.
