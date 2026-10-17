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
| **Shipping:** courier rates, booking, delivery status (the payout clock starts only when the courier confirms delivery) | ✅ (mock in demo; Biteship adapter wired) | Pickup scheduling, prepaid return labels |
| **Payouts:** released to the seller's payment sub-account after delivery **and the return window** (72 h; 168 h for new sellers) | ✅ simulated | Live xenPlatform settlement, confirmed provider-side hold |
| **Returns:** `request_return` for agents, Approve / Dispute / Item received buttons for sellers, operator queue for disputes, return policy shown to agents ([ADR 0007](adr/0007-payout-hold-and-returns.md)) | ✅ (refund is simulated; Xendit refunds are manual) | Automatic Xendit refunds, prepaid return labels, per-buyer limits |
| **Public store link** (`/stores/{id}`, or `<shop>.<domain>` when a base domain is set): a catalog page with search and sort, a product page with photos and option chips, a cart (several products of one shop in one order) and a short checkout, a QRIS pay page that can be saved as an image, order status and a return form that a person opens from an Instagram bio, a WhatsApp status or a QR code; the same pages carry schema.org data and pointers to MCP and UCP for agents. Server-rendered HTML that also works with JavaScript off; one small script makes moving between pages smooth and keeps a waiting order page current. Orders land in the seller's chat like any other | ✅ | Share cards per product, courier choice and an estimate on the cart page, id-ID copy for buyers |
| **Seller channels:** WhatsApp is the default and the sign-in identity; a seller can link a **Telegram** chat and choose it for orders, stock checks and payouts (no 24-hour window, no message fee). One `Messenger` interface, the address picks the channel ([ADR 0008](adr/0008-seller-channels-and-shop-addresses.md)) | ✅ WhatsApp and Telegram | LINE, Zalo, Instagram or Messenger as seller channels; an Instagram DM bot **for buyers**, run per seller on the seller's own account (roadmap, not built) |
| **Shop address:** each shop can claim `rina.<domain>` instead of `/stores/<id>`; short enough to say aloud, and each shop is its own browser origin. The address is also the shop's agent surface: `rina.<domain>/.well-known/ucp`, `/ucp/v1`, `/agent/v1` and `/mcp` see only that shop ([ADR 0008](adr/0008-seller-channels-and-shop-addresses.md)) | ✅ optional (`STORE_BASE_DOMAIN`, needs wildcard DNS and certificate; never tried on a real domain) | Custom domain per shop |
| **Sign-up in chat** with the address chosen first (unique, held 72 hours until verified), limits per hour and per number, lookalike names refused, the shop invisible until verified ([ADR 0010](adr/0010-chat-signup-address-first.md)) | ✅ built, **off by default**, simulated messages only | The payment provider's identity and bank check through a link; abuse reporting; Indonesian wording for refusals |
| **Seller dashboard** with one-time-code sign-in (the code goes to the seller's chosen channel): shop link with copy and share, four tiles for what needs attention (tappable filters), orders, products, return policy, shop address and channel settings | ✅ | Analytics, multi-user shops |
| **Connector API** for OMSs, e-commerce plugins and website builders | ✅ | WooCommerce plugin, Jubelio and Ginee adapters |
| **Google Sheets sync** (OAuth + Picker, `drive.file` scope) | Designed | ✅ |
| **Marketplace stock sync** (Shopee, TikTok Shop partner APIs) | – | ✅ |
| Monthly sales report and commission invoice for tax filing | – | ✅ |

## Key journeys

### 1. Seller onboarding (about 2 minutes)

> **Status: the chat part is built, the verification part is not.** A hybrid: **build the shop in the chat, sell after verification.** Steps 1 to 4 below run in the
> prototype with `SIGNUP_ENABLED=true` (off by default), tested with simulated messages and never with live WhatsApp traffic. Step 5, the payment provider's identity and bank
> check through a link, is **not built**: with the mock payment provider a finished sign-up is verified at once, and with a real one an operator verifies by hand
> (`POST /tasks/verify-seller`). Until then the shop is invisible to buyers and agents. Decision and limits: [ADR 0010](adr/0010-chat-signup-address-first.md); the
> impersonation risks: [doc 09](09-security-privacy-compliance.md#impersonation-and-phishing). Demo sellers are still created with `POST /dev/sellers`.

1. Seller sends "hi" to the Etalase number. The message proves the number (the webhook is signed by Meta), so no code is needed, and the bot asks for a shop name.
   It also says, once, what Etalase never does: ask for a code, PIN or password in chat, or ask anyone to transfer money. Then the bot proposes the shop's address
   from the name (`rina-hijab.<domain>`), or the seller types another; the address must be unique, is claimed atomically, and may not read like a bank, a marketplace or an
   official account. Last, where they ship from (street, city, postal code in one message; buyers only see the city).
2. Sends product photos and one description, typed or as a voice note. Optionally connects Instagram
   (product posts are imported) or a spreadsheet. Only the WhatsApp number is needed to build the shop; selling needs step 5.
3. Gets a summary: products, variants, stock, price, estimated weight, readiness score.
4. Taps **Publish**. The products are saved; the shop is a preview nobody else can see and cannot take an order yet.
5. Opens the verification link, a fixed page on Etalase's own domain that leads to the payment provider, and completes identity and bank checks there. Once the
   provider confirms, the shop is open to buyers and AI agents and can take orders. **Not built**: today the mock provider verifies at once, or an operator does it by hand.
   What is built: a shop that is not `verified` is hidden from search, the store page, its address and the UCP/MCP/REST endpoints, and `create_checkout` refuses it.
   An address not verified within 72 hours (`SIGNUP_HOLD_HOURS`) goes back to the pool.

### 2. Buying through an agent
1. Buyer asks their assistant for a product.
2. The agent calls `search_products`, then `get_shipping_options`.
3. The buyer agrees; the agent calls `create_checkout`, for one product or for several products of the same shop (`items`: one order, one payment, one parcel; all lines are reserved or none). Stock is reserved for 15 minutes.
4. The agent shows the QRIS code; the buyer pays.
5. The seller gets "New paid order" in WhatsApp with **Accept** / **Out of stock** buttons.

### 2b. Buying without an agent (the store link)
1. The seller shares the shop link (the publish message and the dashboard give it, with copy and share buttons, and the seller can ask for it by chat: "link toko").
2. The buyer opens it on a phone, sees the shop (return window, track record, a line saying the seller is paid only after delivery), searches or sorts the products, and opens one.
3. The product page has the photos, the price, the colour or size options as chips (the price and availability change as the buyer picks), the return protection and the seller. **Buy now** opens a short checkout: the item with a quantity stepper, contact, delivery address, QRIS as the payment, the total.
4. The pay page shows the countdown, the total and the QRIS code first (it can be saved as an image to scan from a banking app), then a progress tracker (details, payment, shipping, delivered).
5. The same page shows the tracking number and, after delivery, an **Ask for a return** form (the phone number is the proof, like for an agent).
6. The seller gets the same alerts on their channel. The seller's number is never on the page.

### 2c. Buying through Google's AI surfaces (UCP): the design, not an integration

> **Status: not integrated.** No Google surface (AI Mode, the Gemini app) calls Etalase today, and UCP agent checkout has no date in Indonesia
> ([doc 16](16-ucp.md), [doc 17](17-market-fit-and-direction.md)). What follows is the flow the UCP specification describes, played by a test client
> against our server (our own tests, plus the official conformance suite where it applies). How Google would show each step is Google's decision.

1. The buyer asks the AI for a product. The surface finds the business through its UCP profile, `/.well-known/ucp` (on a shop's own address, that shop's profile).
2. The surface creates a checkout session with the products, asks the buyer for a name, a phone number and a delivery address (all required: the courier and
   the seller call the buyer), and shows the courier options Etalase returns as fulfillment options. The buyer, not the seller, picks one; otherwise the cheapest is used.
3. The buyer agrees and the surface completes the session. Etalase reserves the stock of every line together and creates the QRIS payment.
4. Etalase answers `requires_escalation` with a `continue_url`. The surface hands the buyer over to Etalase's payment page, because QRIS has to be paid in a
   banking or e-wallet app, not inside a chat. A surface that only completes with a card or an AP2 mandate cannot use Etalase (8 of the 21 failing conformance tests).
5. The buyer pays. The session becomes `completed` with the order, and the seller gets "New paid order" in their chat.

Until a surface does this, the same journey is available to any MCP or REST agent (section 2), and to a person through the store link (section 2b).

### 3. Confirm-first (uncertain stock)
If stock is untracked, stale (over 3 days) or nearly sold out (2 or fewer), the seller is asked "Is it still available?" **before** the buyer is asked to pay. No refunds needed when stock turns out to be gone.

The seller has `SELLER_CONFIRM_TIMEOUT_MINUTES` (60 by default) to answer. Once, after 15 minutes (`SELLER_CONFIRM_REMINDER_MINUTES`, 0 turns it off), the seller gets the same question again; if the window ends with no answer the check expires, the stock is released, the seller is told it ran out of time and nothing is charged, because the payment code only exists after the seller says yes. The buyer's agent sees the deadline while it waits and, afterwards, that the seller did not answer (not that a payment timed out); the store page says the same to a person.

### 4. Shipping and payout
The seller replies "tracking JNE123456". When the courier reports delivery, the order moves to `delivered` and the seller is told the date the money will be paid out. After the return window (72 hours; 168 for a seller with fewer than 5 completed orders) the payout (order value minus commission) is released to the seller's payment sub-account and the seller is notified.

Who picks the courier: the buyer or the buyer's agent (`courier` over REST and MCP, the fulfillment option over UCP); with no choice the cheapest rate is used, and the store pages always use the cheapest. The list of couriers is one list for the whole platform. A seller cannot restrict it or bring their own courier in this version, and the live Biteship rates have never been run.

### 5. After delivery: returns
1. The buyer's agent reads the seller's `return_policy` and `track_record` before buying, and `get_checkout_status` tells it until when a return can be requested.
2. If the item is damaged, wrong or not as described (or the seller accepts change-of-mind returns), the agent calls `request_return` with the checkout id, the buyer's phone, a reason, a note and up to five photo links.
3. The seller gets **Approve return** / **Dispute** buttons. Silence for 48 hours counts as approval.
4. The buyer sends the item back; the agent may report the tracking number. The seller taps **Item received** and the buyer is refunded. Commission is not charged on a refunded order.
5. Dispute, or an approved return that never arrives but has a tracking number: an operator decides (`refund` or `release`).
6. A parcel the courier returns to the seller is refunded automatically and the stock restored.

Sellers set only one thing: whether to accept returns for a change of mind (by WhatsApp: "accept returns for change of mind"), plus a short note.

## UX principles

- **Zero new apps for sellers.** Everything important happens in the chat app they already use (WhatsApp by default, Telegram if they prefer); the dashboard is optional.
- **Buttons for decisions, chat for data.** Accept, Out of stock and Publish are buttons; stock and prices can be typed naturally.
- **Confirm before changing anything important,** and never change payout details by chat.
- **Never leave money in limbo:** every order that is delivered, returned or stuck ends in a payout, a refund or an operator alert.
- **Honest availability for agents:** `in_stock`, `confirm_with_seller` or hidden when sold out.
- **Bilingual:** English by default for the demo; Bahasa Indonesia for Indonesian sellers (`SELLER_LOCALE=id`).
- **Buyer pages are built for a phone on a weak connection, and to look like a shopping app people already use, not a template** (design system v3, `gateway/pages.py`):
  one inline stylesheet, one self-hosted font (Plus Jakarta Sans, about 27 KB, SIL Open Font License), white sections ("bands") separated by grey gaps on a phone and
  rounded cards on a wider screen, near-black ink, and one green used only for the main action. The store page has a gradient cover and avatar (a colour from the shop name,
  so every shop looks different without photos), three facts (products, return window, orders done), search and sort when there is more than one product, and a
  two-column grid of square photos with name and price. The **product page** is where the decision happens: swipeable photos with a counter, price, option chips, a
  sticky Buy bar, return protection and a rail of more products from the shop. The **checkout** is one screen of bands with a sticky total-and-button bar; errors mark the
  field and move focus to it. The **pay page** puts the countdown and the QR code above the fold and lets the buyer save the code as an image.
  It feels smooth because links and forms are fetched and swapped in place with a short cross-fade (View Transitions where the browser has them, the back button restores
  the previous page and what was typed, store links are fetched when the pointer reaches them), a thin progress line shows on a slow server, photos fade in once loaded,
  a waiting order swaps its status in place (with a live countdown to the payment deadline), and a submit button shows a spinner and ignores a second tap. Sorting
  reorders the real cards with a short move animation. Without JavaScript every link and form still works as a normal page. All motion is off for people who ask for
  reduced motion, hover effects exist only on devices that hover, controls are at least 44 px high with pressed, busy and keyboard-focus states, prices use tabular
  figures, and every error says what to fix. The same tokens, buttons, fields and icons are used on the store, product, checkout, pay, return and policy pages, the UCP
  hosted payment page and the seller dashboard.
- **The seller dashboard is for a seller with a phone and two minutes:** a greeting and the shop link first (copy, share, open), then four tiles that double as filters
  (To confirm, To ship, Returns open, Paid out), orders as cards that say what is waiting on whom (the ones that need the seller come first, with a strip saying what to do),
  products with a plain-language reason when one is not ready for agents, and settings (returns and payouts, shop address, channel). It refreshes quietly every 20 seconds.
- **The page copy is English.** Buyer pages and the dashboard are in English like all code, docs and the deck, because the judges are regional; seller chat messages
  switch to Bahasa Indonesia with `SELLER_LOCALE=id`. Indonesian buyer-page copy is the first thing to add before a pilot.
