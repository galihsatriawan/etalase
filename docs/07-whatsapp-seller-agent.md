# 7. Seller experience (WhatsApp, Telegram) and the seller agent

Code: `prototype/etalase/seller/`, `prototype/etalase/agents/seller_agent.py`.

## How a message is handled

```
WhatsApp Cloud API webhook  ─►  parse_whatsapp_webhook  ─┐
Telegram Bot API webhook    ─►  parse_telegram_update    ─┴►  SellerInbox.handle      (the sender is the channel address)
   │
   ├─ unknown number        → sign-up in chat when SIGNUP_ENABLED (seller/signup.py), else a pointer to the dashboard   (unknown Telegram chat: "/start <code>" links it, anything else gets one pointer)
   ├─ number still signing up → the next sign-up question (name, address, where they ship from)
   ├─ button reply          → deterministic handler (Publish, Available, Sold out, Accept, Out of stock)
   ├─ photo / voice / file  → saved, grouped with the next description
   └─ text
        ├─ Gemini configured → ADK seller agent (tools below)
        └─ offline           → intent parser (same actions)
```

**Buttons never go through the model.** Accepting an order or confirming stock must be exact.

## The ADK seller agent

One `LlmAgent` with six tools. The seller id comes from the verified WhatsApp number and lives in
session state; it is **not** a tool argument, so the model cannot act on another seller's data.

| Tool | Does |
|---|---|
| `build_catalog(description)` | Runs Gemini extraction on the description plus recent photos and voice notes; sends the summary with Publish / Edit buttons |
| `update_stock(color, quantity, mode, product_name?)` | Set or add stock; refused for OMS / store sellers |
| `update_price(product_name, price_idr)` | Change a price |
| `submit_tracking(waybill, order_id?)` | Mark the oldest accepted order (or a given one) as shipped |
| `get_sales_summary()` | Orders, revenue and payouts |
| `set_return_policy(accepts_change_of_mind, note?)` | Whether buyers may return items for a change of mind, plus a short note shown to agents |

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
| Published | How many products are live, **and the shop link** (also on request: "link toko" / "shop link") | – |
| Confirm-first order | "Quick stock check… Is it still available?" | Available · Sold out |
| Paid order (instant) | "New paid order…" | Accept · Out of stock |
| Accepted | Ship-to address, "reply 'tracking <number>'" | – |
| Delivered | "Payout of <amount> on <date> if the buyer does not open a return" | – |
| Payout released | Payout amount and commission | – |
| Return requested | Reason, the buyer's note, "approve or dispute within 48 hours" | Approve return · Dispute |
| Return approved | "Tap the button when the parcel is back" | Item received |
| Return disputed / refunded / shipped back / parcel returned by courier | Short status notes (best effort, no template) | – |

All templates live in `seller/i18n.py` in English and Bahasa Indonesia (`SELLER_LOCALE`).

## WhatsApp rules and costs

- Messages that start a conversation (for example a new-order alert outside the 24-hour window) must
  use **approved templates**; replies inside the window can be free-form. The adapter tries free-form first and falls back
  to the template on error 131047; the six utility templates (stock check, new paid order, ship now, payout, return requested, return approved) are defined in `adapters/whatsapp_templates.py`.
- Meta charges per business message, and its pricing changed in October 2026; what exactly changed for replies inside the 24-hour window is reported
  differently by Meta's own page and by business-API resellers, so treat it as unverified and see [doc 10](10-business-model.md#whatsapp-cost-and-other-channels).
  Notifications are kept short and combined, and order alerts use the *utility* category.
- Media URLs from the webhook expire, so files are downloaded immediately.
- Webhooks are verified with the `X-Hub-Signature-256` header when `WHATSAPP_APP_SECRET` is set.

## Channels: WhatsApp and Telegram

WhatsApp is the default and the identity (dashboard sign-in, and sign-up in chat when it is switched on: [doc 02](02-product.md#1-seller-onboarding-about-2-minutes), [ADR 0010](adr/0010-chat-signup-address-first.md)). A seller can also **link a Telegram chat** and choose it for orders, stock checks and payouts
([ADR 0008](adr/0008-seller-channels-and-shop-addresses.md)). The code is `seller/channels.py` and `adapters/telegram.py`.

1. In the dashboard (Settings → Where Etalase messages you) the seller taps **Connect Telegram**. `POST /api/me/telegram/link` returns a signed code valid for 10 minutes and a
   deep link `t.me/<bot>?start=<code>`.
2. The seller taps Start in Telegram. Telegram delivers `/start <code>` to `POST /webhooks/telegram`; the inbox sees a chat nobody has linked, verifies the code and stores the chat id on
   the seller. The page notices within a few seconds without a reload.
3. `PUT /api/me/channel` switches between `whatsapp` and `telegram` at any time. Etalase confirms in the new channel.

What differs from WhatsApp: no 24-hour window, no templates (the `Template` argument is ignored), no per-message fee, inline buttons (two per row, 64 bytes of callback data,
which the existing button ids fit), 4,096 characters per message, and only **private chats** count (a bot added to a group takes no orders). Photos, voice notes and CSV files work as on
WhatsApp (`getFile`, up to 20 MB). The webhook must carry the secret set with `setWebhook` (`TELEGRAM_WEBHOOK_SECRET`, header `X-Telegram-Bot-Api-Secret-Token`).

Replies go back through the channel the seller wrote on; messages Etalase starts go to the chosen channel. A seller can write on both.
A link code works once and lasts 10 minutes; it is shown only inside a signed-in session. Linking a different chat later is announced to the old chat and to the WhatsApp number. If the linked chat becomes unreachable (the seller blocked the bot), messages and the sign-in code go to the WhatsApp number instead.

## Seller dashboard sign-in

The dashboard (`/`) uses a **one-time code sent from Etalase's own number or bot** (WhatsApp by default, Telegram once the seller linked it) to the seller. The seller's own
WhatsApp account is never connected or logged into. The sign-in identity stays the WhatsApp number.

1. Seller enters their number → `POST /auth/otp` (same response whether or not the number exists).
2. Etalase sends a 6-digit code, valid 5 minutes, max 5 attempts (production: an *authentication* template).
3. `POST /auth/verify` returns a signed session token.
4. Sensitive actions (payout account changes) need extra verification at the payment provider.

## Seller dashboard

After signing in, the dashboard (`prototype/etalase/api/dashboard.html`, one file, no build step) shows:

- **The shop link** with Copy, Share and Open buttons. Put it in an Instagram bio or a WhatsApp status.
- **What needs attention**: four tiles (to confirm, to ship, returns open, paid out). The first three are filters for the order list. The buttons for these actions are in the
  chat messages; the dashboard shows and counts them.
- **Orders** as cards: the buyer's first name and city, how long ago, a status in plain words, the items, the total and, for orders waiting on the seller, a strip saying what to do.
  Return reasons, the buyer's note and photo links (escaped, https only, never pushed into chat) and the payout are shown on the card. Orders that need the seller come first.
- **Products** with photo, status, agent-readiness score, a plain-language reason when one is not ready, stock per variant, and a link to see it in the shop.
- **Settings**: returns and payouts (a switch for change-of-mind returns and a note of 300 characters that buyers see labelled as the seller's own words; saved with
  `PUT /api/me/return-policy`, the button is enabled only when something changed); the **shop address** (only when a base domain is configured; `PUT /api/me/slug`); and the
  **channel** (WhatsApp or Telegram).

Sign-in has labelled fields, a clear message when the code cannot be sent (the seller has to message Etalase first, because of the 24-hour window)
and no unhandled errors.
