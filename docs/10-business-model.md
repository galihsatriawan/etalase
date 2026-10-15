# 10. Business model and cost

## Pricing

- **Commission per paid-out order, 3–5%** (4% in the prototype), excluding VAT. It is taken when the payout is released, after delivery and the return window, so a refunded order earns nothing.
- No monthly fee, no listing fee: sellers pay only when something sells.
- Shipping is paid by the buyer.
- **QRIS fees.** Since 1 October 2026 Bank Indonesia sets the QRIS merchant discount rate (MDR) at 0%
  for micro merchants on transactions up to Rp500,000 and for all merchants up to Rp100,000; above
  that it is 0.3% (micro) or 0.7% (others). Most Etalase sellers are micro merchants with orders below
  Rp500,000, so the MDR is usually 0%. Payment gateways may add a fixed processing fee (Xendit's list
  price is IDR 4,000 per transaction): in the pilot it is shown to the buyer as a payment fee while we
  negotiate volume pricing or compare Midtrans. Sources: [Kontan](https://keuangan.kontan.co.id/news/kebijakan-mdr-qris-0-berlaku-mulai-1-oktober-2026-btn-sesuaikan-tarif-merchant),
  [Xendit pricing](https://xendit.co/en-id/pricing/), [Midtrans](https://docs.midtrans.com/docs/what-is-the-applicable-transaction-fee-for-qris).

For comparison, Shopee Indonesia's admin fee for fashion is 10% for non-Mall sellers after the 2026
revision, before other service fees ([Metro TV News](https://www.metrotvnews.com/read/NxGCP3en-shopee-kerek-biaya-admin-mulai-2026-segini-nominalnya)). The pitch is not "free" but **cheaper, pay-per-sale, on a channel the
seller owns**.

### Against the tools sellers can buy today

Checked on 2026-10-15 on the vendors' own pages. Prices change; re-check before quoting.

| Product | What it is | Price on its page | What it means for us |
|---|---|---|---|
| [cshub](https://cshub.id/pricing) | AI customer service and order creation inside WhatsApp, payment gateway integration | Starter Rp300,000 / month (1,000 transactions, 20 products); Migration Rp700,000 (the FAQ says Rp500,000); Business Rp2,500,000. The page says prices are estimates | A seller who sells Rp7.5 million a month pays us the same Rp300,000 as Starter (4% commission). Below that we are cheaper, above it dearer. Its page mentions no payout hold or return flow, which does not prove it has none |
| [Jubelio](https://jubelio.com/harga) | Order and inventory system for marketplaces, social commerce and web stores | Rp150 per order, terms apply | A back-office for sellers who already sell on many channels. It makes Jubelio a partner (Connector API), not a competitor for the same seller |
| Shopee (fashion, non-Mall) | Marketplace | About 10% admin fee before other service fees (see above) | Our price is lower, but a marketplace brings its own buyers |

The honest comparison is not "cheaper than cshub" at every volume. Etalase's difference is the **agent-facing storefront** (MCP and UCP) and a
**payout hold plus returns** that a chat-bot subscription does not include. Whether sellers value that is exactly what the pilot has to test
([doc 17](17-market-fit-and-direction.md)).

## Who pays (B2B2C)

| Segment | Pays | How |
|---|---|---|
| WhatsApp / Instagram sellers | Seller | Commission per order |
| OMS and website-builder partners | Partner or their merchants | Revenue share or per-store fee through the Connector API |
| Later: banks, e-wallets, payment providers | Partner | Merchant acquisition into digital payments |

Additional revenue later: an optional monthly plan for multi-channel sync and analytics.

## Pilot cost estimate

Assumptions: 100 sellers, 3,000 products, 1,000 orders a month. Rough estimates to validate with the
Google Cloud Pricing Calculator. FX assumption US$1 ≈ IDR 16,000.

| Component | Basis | Est. / month |
|---|---|---|
| Cloud Run, Firestore, Storage, Pub/Sub | Low pilot traffic, mostly within free tiers | US$10–30 |
| Gemini on Vertex AI | ~3,000 catalog extractions + ~2,000 seller-agent turns (buyers' agents run on their own models) | US$15–60 |
| WhatsApp Cloud API | ~4 utility messages per order × 1,000 orders at ~IDR 366–460 (free inside an open service window, **unverified after October 2026**, see below) | ~US$90–115 (up to ~US$225 if replies in the window are charged) |
| Biteship rates API | ~5,000 rate checks at ~IDR 5 | < US$2 |
| QRIS payments | MDR 0% for micro sellers up to Rp500,000 (Bank Indonesia, from Oct 2026); gateway processing fee shown to the buyer | Pass-through |
| **Total platform cost** | | **≈ US$120–210** |

**Unit economics:** platform cost ≈ IDR 2,000–3,500 per order (WhatsApp ≈ IDR 1,460–1,840, cloud
and Gemini the rest); commission at 4% on an IDR 170,000 order = IDR 6,800. WhatsApp is most of the
cost per order, so the design keeps seller messages short, combined and inside the 24-hour window
where possible. If the gateway's fixed fee were absorbed instead of passed on, the margin would
disappear at list price, which is why gateway pricing is on the pilot validation list.

Price references: [Meta WhatsApp pricing](https://developers.facebook.com/docs/whatsapp/pricing),
[Qontak (Indonesia rates, Oct 2026)](https://help-center.qontak.com/hc/en-us/articles/48430892733465-Latest-WhatsApp-Message-Prices-October-2026),
[SleekFlow rate card](https://help.sleekflow.io/en_US/daftar-harga),
[Biteship API](https://biteship.com/en/product/api),
[Bank Indonesia QRIS MDR via Kontan](https://keuangan.kontan.co.id/news/kebijakan-mdr-qris-0-berlaku-mulai-1-oktober-2026-btn-sesuaikan-tarif-merchant),
[Xendit pricing](https://xendit.co/en-id/pricing/).

## WhatsApp cost and other channels

**What changed.** Meta changed WhatsApp business pricing in October 2026. The sources disagree on what it means for replies inside the 24-hour window:
Meta's pricing page says non-template messages are free, while two business-API resellers (Qontak, SleekFlow) list a "service" price from 1 October 2026
(about Rp357 on Qontak's table). The evidence rows are in [doc 15](15-evidence.md). **Unverified until the provider we use confirms it.**

**What it would cost us** (our arithmetic, an estimate): about 4 utility templates per order at Rp366–460 is Rp1,460–1,840. If replies in the window were also charged
at about Rp357 and a seller exchanged 3–5 of them per order, that adds Rp1,070–1,785, so **about Rp2,500–3,600 per order in the bad case**, against a 4% commission of
Rp6,800 on a Rp170,000 order: 37–53% of the commission (the good case is 21–27%). The design answer does not change: fewer, combined messages, and decisions as buttons.

| Lever | Status |
|---|---|
| Utility templates only for alerts the seller must see (stock check, new paid order, ship now, return) | Built |
| Buyers never receive a WhatsApp message from us: they use the order page (`/pay/{id}`) or their agent | Built |
| One combined reply per photo burst; a per-seller message meter | Proposed, not built |
| A second seller channel with no window and no fee (Telegram) | Built, optional |
| Dashboard shows what needs attention, so a seller who already looks at it needs no reminder | Built (the buttons stay in WhatsApp) |

**Do we keep WhatsApp?** Yes, as the seller's channel. Sellers are already there (BPS: 95.33% of e-commerce businesses sell through instant messaging), and WhatsApp is the only
one of the Meta channels where an order alert can reliably reach a seller **outside** the 24-hour window, through an approved utility template.

**Click-to-WhatsApp (CTWA) ads and the free window.** Business-API resellers describe a free "entry point" window for a conversation that starts from a CTWA ad (respond.io: up to
7 days, extended from 72 hours; Wati: 72 hours), and they disagree on whether service messages keep any free allowance after 1 October ([doc 15](15-evidence.md)). Meta's own pricing page, as
far as we could read it, still showed the old rules. **Unconfirmed.** It matters to us in a narrower way than "use CTWA to be free": Etalase's alerts go to a *seller*, on events (a paid order)
that no ad opens, so a CTWA window does not make them free. It can make the seller's **first chat** with Etalase free if Etalase runs the ad that brings the seller in, and it makes the *buyer* chats
of sellers who run CTWA ads free to those sellers, in their own WhatsApp. So the design answer stays the same: few, combined messages, buttons for decisions, and a second channel that has no per-message fee.

**A second seller channel (built): Telegram.** The `Messenger` interface now has two real adapters. A seller can link a Telegram chat from the dashboard and choose it for orders, stock checks and payouts
([doc 07](07-whatsapp-seller-agent.md#channels-whatsapp-and-telegram), [ADR 0008](adr/0008-seller-channels-and-shop-addresses.md)). A Telegram bot can message a user at any time once the user started it: no
24-hour window, no templates, no per-message fee (Telegram's Bot API pages describe no price). It is the cheapest way to show that the seller side is a channel and that cost can be moved. It is not
claimed to be where Indonesian sellers are: WhatsApp is, and stays the default and the sign-in identity.

**Instagram and Facebook.** What Meta's own pages say (read 2026-10-15 and 2026-10-16, [doc 15](15-evidence.md)): Instagram messaging is for professional accounts, works inside a 24-hour
window, one-time notifications are not available on it, and "some features may not work properly until your app has been granted Advanced Access", which an app serving accounts it does not own needs;
Messenger has the same 24-hour window, with a Human Agent tag and utility templates; neither page states a price. "Instagram DMs are free" came from a vendor table we cannot confirm: **unverified**.
Our reading, **not verified with Meta**: an automatic "new paid order" alert to a seller who has been quiet for a day could not be sent reliably on Instagram, and that is exactly the alert that must not be lost. So:

- **Instagram as an input and a door, not as the alert channel** (built): import product posts as drafts, and put the store link in the bio (the shop address, `rina.<domain>`, is short enough to say aloud).
- **The store link makes Instagram and Facebook sellers reachable now**, because the buyer pays and tracks on our page and the seller still gets the alert on their channel.
- **A buyer-side Instagram DM bot is a different product, and is not built.** Two directions have two rules: seller ↔ Etalase can use one shared number or bot for all sellers (the seller is our customer); buyer ↔ seller
  must run **per seller, on that seller's own account** through the seller's OAuth grant, because a buyer talks to `@toko-rina` and never to an "Etalase" account. It could reuse the agent tools
  (search, product, shipping, checkout) and send the checkout link, since payment cannot happen inside a DM. Against it: customer-service chatbots for WhatsApp and Instagram are a crowded category (cshub, Cekat.ai and others),
  it needs Advanced Access, and it blurs the agent-commerce story. It stays on the roadmap (phase 3).
- **Instagram or Messenger as a *seller* alert channel** stays on the roadmap (phase 4, behind the same interface) until the out-of-window rules are checked.

## AI cost per unit

Most of the expensive AI work happens **outside Etalase**: the buyer's conversation runs on the
buyer's agent (for example Gemini), and our MCP tools are plain database queries. Etalase pays for
Gemini only in two places:

| Workload | Tokens (estimate) | Gemini 2.5 Flash | Gemini 3.8 Flash from 2027 |
|---|---|---|---|
| Catalog extraction, once per product (3 photos + 20 s voice note + text → JSON) | ~4,500 in / ~800 out | ≈ US$0.004 (≈ IDR 65) | ≈ US$0.013 (≈ IDR 210) |
| Seller agent per order (≈ 2 free-text turns, 2 model calls each) | ~12,000 in / ~800 out | ≈ US$0.006 (≈ IDR 90) | ≈ US$0.024 (≈ IDR 380) |

Prices from the [Vertex AI pricing page](https://cloud.google.com/vertex-ai/generative-ai/pricing)
(Gemini 2.5 Flash: US$0.30 / 1M input, US$1.00 audio input, US$2.50 output; Gemini 3.8 Flash:
US$0.75 / 3.75 introductory until 31 December 2026, then US$1.50 / 7.50). Even at 2027 list prices,
AI is about 6% of a 4% commission on an IDR 170,000 order.

**Keeping it low as volume grows**

- Buttons and the offline parser handle common commands without a model call; the model is the fallback.
- Use Flash-Lite for routing and short replies, Flash for extraction.
- Batch / flex pricing (50% off) for bulk catalog imports; context caching (about 10% of the input
  price) for the agent's instructions and tool definitions.
- A free allowance per seller (for example the first 50 products), then a paid plan for very large
  catalogs, so sellers who never sell cannot generate unbounded AI cost.

The larger risks to margin are **WhatsApp messaging** (about half of the cost per order) and fixed
costs such as seller onboarding and support, not model inference.

## Tax

See [Security, privacy and compliance → Tax](09-security-privacy-compliance.md#tax). In short:
commission is quoted excluding VAT, sellers handle their own income tax with Etalase's monthly
reports, and marketplace PPh 22 collection (postponed to at least November 2026) is watched through
the country pack's `tax_profile`.

## Go-to-market

1. **Pilot in one category and city** (fashion / hijab sellers in Jakarta and Bandung) through seller
   communities and WhatsApp groups.
2. **Partners:** payment providers and local website builders via the Connector API.
3. **Distribution through partner app stores** (OMS, marketplace partner programmes) for stock sync.

## Assumptions to validate first

1. Sellers' willingness to pay 3–5% per order.
2. Share of sellers who track stock at all.
3. When UCP checkout reaches Indonesia.
4. Payment provider support for sub-accounts and **delayed release** (the hold in [ADR 0007](adr/0007-payout-hold-and-returns.md)) for our use case.
5. Whether sellers accept waiting 3 to 7 days after delivery for their money, in exchange for buyers who trust a store they have never heard of.
6. Cost of a refunded order. Xendit's pages say the payment fee and VAT come back only on a full refund within 24 hours, so a return refund
   usually costs the fee. Who bears it (seller, Etalase, or a share of the commission) is not decided. Refund disputes also cost operator time, which is not in the table above.
7. Return rate in the pilot category. Fashion has high return rates on marketplaces; we have no number for WhatsApp sellers and will not quote one until we measure it.
