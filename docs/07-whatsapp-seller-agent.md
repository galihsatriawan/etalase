# 7. WhatsApp seller experience and the seller agent

Code: `prototype/etalase/seller/`, `prototype/etalase/agents/seller_agent.py`.

## How a message is handled

```
WhatsApp Cloud API webhook  ─►  parse_whatsapp_webhook  ─►  SellerInbox.handle
   │
   ├─ unknown number        → registration link
   ├─ button reply          → deterministic handler (Publish, Available, Sold out, Accept, Out of stock)
   ├─ photo / voice / file  → saved, grouped with the next description
   └─ text
        ├─ Gemini configured → ADK seller agent (tools below)
        └─ offline           → intent parser (same actions)
```

**Buttons never go through the model.** Accepting an order or confirming stock must be exact.

## The ADK seller agent

One `LlmAgent` with five tools. The seller id comes from the verified WhatsApp number and lives in
session state; it is **not** a tool argument, so the model cannot act on another seller's data.

| Tool | Does |
|---|---|
| `build_catalog(description)` | Runs Gemini extraction on the description plus recent photos and voice notes; sends the summary with Publish / Edit buttons |
| `update_stock(color, quantity, mode, product_name?)` | Set or add stock; refused for OMS / store sellers |
| `update_price(product_name, price_idr)` | Change a price |
| `submit_tracking(waybill, order_id?)` | Mark the oldest accepted order (or a given one) as shipped |
| `get_sales_summary()` | Orders, revenue and payouts |

Instruction highlights: reply in the seller's language, ask when ambiguous, never invent numbers,
never change payout details in chat.

**Why one agent, not many:** speed on WhatsApp, lower cost and easier debugging; the deterministic
parts (payments, buttons, reservations) stay in code. Separate agents make sense later for
payout reconciliation or analytics ([ADR 0004](adr/0004-one-agent-deterministic-handlers.md)).

## The buyer agent (demo)

`agents/buyer_agent.py` is a Gemini agent that shops only through Etalase's public MCP server
(`McpToolset` over streamable HTTP). It stands in for Gemini / AI Mode until UCP checkout is
available in Indonesia, and proves that any MCP-capable agent can buy.

## Messages and buttons

| Moment | Message | Buttons |
|---|---|---|
| Catalog drafted | Products, variants, stock, price, weight, readiness | Publish · Edit |
| Confirm-first order | "Quick stock check… Is it still available?" | Available · Sold out |
| Paid order (instant) | "New paid order…" | Accept · Out of stock |
| Accepted | Ship-to address, "reply 'tracking <number>'" | – |
| Delivered | Payout amount and commission | – |

All templates live in `seller/i18n.py` in English and Bahasa Indonesia (`SELLER_LOCALE`).

## WhatsApp rules and costs

- Messages that start a conversation (for example a new-order alert outside the 24-hour window) must
  use **approved templates**; replies inside the window can be free-form.
- Meta charges per business message; from October 2026 this includes service replies. Notifications
  are kept short and combined, and order alerts use the cheaper *utility* category.
- Media URLs from the webhook expire, so files are downloaded immediately.
- Webhooks are verified with the `X-Hub-Signature-256` header when `WHATSAPP_APP_SECRET` is set.

## Seller dashboard sign-in

The dashboard (`/`) uses a **one-time code sent from Etalase's WhatsApp Business number** to the
seller's number. The seller's own WhatsApp account is never connected or logged into.

1. Seller enters their number → `POST /auth/otp` (same response whether or not the number exists).
2. Etalase sends a 6-digit code, valid 5 minutes, max 5 attempts (production: an *authentication* template).
3. `POST /auth/verify` returns a signed session token.
4. Sensitive actions (payout account changes) need extra verification at the payment provider.
