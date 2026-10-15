# 2. Product

## One-line pitch

Etalase turns a WhatsApp seller's product photos and chats into a storefront that AI shopping agents can find, check stock on and buy from, with every order landing back in the seller's WhatsApp.

## Who it is for

| Persona | Need | What Etalase gives them |
|---|---|---|
| **Rina, WhatsApp seller** (hijab shop, sells from her phone, catalog in photos) | More buyers without marketplace fees or new apps | Catalog from photos in minutes, orders and payouts in WhatsApp |
| **Dewi, buyer** | Ask an assistant for "a black voal hijab under Rp100k" and just buy | Small sellers show up in her agent's results, with QRIS payment |
| **Buyer's AI agent** (Gemini or any MCP client; UCP checkout) | Clean product data and a reliable checkout | MCP tools and a UCP profile and checkout with stock-aware reservation |
| **Builder or OMS partner** | "AI-agent ready" for all their merchants without building it | Connector API with an OpenAPI spec and signed order webhooks |

## Features

| Feature | MVP (hackathon) | Later |
|---|---|---|
| **AI catalog builder:** photos, voice notes, text and Excel/CSV become products, variants, prices and weights (Gemini multimodal, structured output) | ✅ | Product photo clean-up, attribute enrichment |
| **Instagram import (optional):** product posts (photo + caption) become drafts; promo posts are skipped | ✅ (sample posts offline, Graph API wired) | Auto-detect new posts |
| **Agent-readiness score** 0–100 with what is missing | ✅ | Benchmarks per category |
| **Agent gateway:** MCP server, REST mirror, UCP profile and Checkout (REST), schema.org data | ✅ | Merchant Center feed, AP2 payment mandates |
| **WhatsApp seller agent** (ADK): stock and price by chat, tracking numbers, sales questions | ✅ | Proactive restock reminders |
| **Safe checkout:** stock reservation, instant vs confirm-first, QRIS | ✅ (payment in test mode) | E-wallets, virtual accounts |
| **Shipping:** courier rates, booking, delivery status (payout only after the courier confirms delivery) | ✅ (mock in demo; Biteship adapter wired) | Pickup scheduling, returns |
| **Payouts:** released to the seller's payment sub-account after delivery | ✅ simulated | Live xenPlatform settlement |
| **Seller dashboard** with WhatsApp one-time-code sign-in | ✅ | Analytics, multi-user shops |
| **Connector API** for OMSs, e-commerce plugins and website builders | ✅ | WooCommerce plugin, Jubelio and Ginee adapters |
| **Google Sheets sync** (OAuth + Picker, `drive.file` scope) | Designed | ✅ |
| **Marketplace stock sync** (Shopee, TikTok Shop partner APIs) | – | ✅ |
| Monthly sales report and commission invoice for tax filing | – | ✅ |

## Key journeys

### 1. Seller onboarding (about 2 minutes)
1. Seller registers with a WhatsApp number (identity and bank verification happen at the payment provider).
2. Sends product photos and one description, typed or as a voice note. Optionally connects Instagram
   (product posts are imported) or a spreadsheet. Only the WhatsApp number is required.
3. Gets a summary: products, variants, stock, price, estimated weight, readiness score.
4. Taps **Publish**. The shop is now visible to AI agents.

### 2. Buying through an agent
1. Buyer asks their assistant for a product.
2. The agent calls `search_products`, then `get_shipping_options`.
3. The buyer agrees; the agent calls `create_checkout`. Stock is reserved for 15 minutes.
4. The agent shows the QRIS code; the buyer pays.
5. The seller gets "New paid order" in WhatsApp with **Accept** / **Out of stock** buttons.

### 3. Confirm-first (uncertain stock)
If stock is untracked, stale (over 3 days) or nearly sold out (2 or fewer), the seller is asked "Is it still available?" **before** the buyer is asked to pay. No refunds needed when stock turns out to be gone.

### 4. Shipping and payout
The seller replies "tracking JNE123456". When the courier reports delivery, the payout (order value minus commission) is released to the seller's payment sub-account and the seller is notified.

## UX principles

- **Zero new apps for sellers.** Everything important happens in WhatsApp; the dashboard is optional.
- **Buttons for decisions, chat for data.** Accept, Out of stock and Publish are buttons; stock and prices can be typed naturally.
- **Confirm before changing anything important,** and never change payout details by chat.
- **Honest availability for agents:** `in_stock`, `confirm_with_seller` or hidden when sold out.
- **Bilingual:** English by default for the demo; Bahasa Indonesia for Indonesian sellers (`SELLER_LOCALE=id`).
