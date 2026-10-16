# 10. Business model and cost

## Pricing

- **Commission per paid-out order, 3–5%** (4% in the prototype), excluding VAT. It is taken when the payout is released, after delivery and the return window, so a refunded order earns nothing.
- No monthly fee, no listing fee: sellers pay only when something sells.
- Shipping is paid by the buyer.
- **QRIS fees.** Since 1 October 2026 Bank Indonesia sets the QRIS merchant discount rate (MDR) at 0%
  for micro merchants on transactions up to Rp500,000 and for all merchants up to Rp100,000; above
  that it is 0.3% (micro) or 0.7% (others) ([Bank Indonesia press release 28/159/DKom, 17 Aug 2026](https://www.bi.go.id/id/publikasi/ruang-media/news-release/Pages/sp_2815926.aspx)).
  The release does not define "micro" or say who classifies a sub-merchant behind a payment gateway. A typical Rp170,000 order is above the Rp100,000 threshold for all merchants,
  so the MDR is 0% **only if the gateway classifies the seller as a micro merchant**; otherwise it is 0.7% (Rp1,190). Mayar's and Midtrans' pages still showed 0.7% when we read them.
  We are asking the gateway how it classifies sub-merchants. Xendit's global English price list also shows a fixed "Xendit Processing fee IDR 4,000", while its Indonesian page shows QRIS at 0.7% including tax and no fixed fee;
  whether the IDR 4,000 applies to an Indonesian contract is **unconfirmed**. In the pilot plan it is shown to the buyer as a payment fee while we ask Xendit and compare Midtrans.
  Sources: [Kontan](https://keuangan.kontan.co.id/news/kebijakan-mdr-qris-0-berlaku-mulai-1-oktober-2026-btn-sesuaikan-tarif-merchant),
  [BCA](https://www.bca.co.id/en/informasi/news-and-features/2026/09/01/18/50/informasi-penyesuaian-merchant-discount-rate-mdr-transaksi-qris),
  [Xendit pricing](https://xendit.co/en-id/pricing/), [Midtrans](https://docs.midtrans.com/docs/what-is-the-applicable-transaction-fee-for-qris).

For comparison, Shopee Indonesia's admin fee for category A (including fashion) is 10.0% for non-Mall sellers after the 2026
revision, plus Rp1,250 per completed order ([Metro TV News](https://www.metrotvnews.com/read/NxGCP3en-shopee-kerek-biaya-admin-mulai-2026-segini-nominalnya)); Shopee's range across categories is 2.5–11.7% and TikTok Shop non-Mall is 9–10% plus a dynamic commission
([Bisnis.com](https://teknologi.bisnis.com/read/20260521/266/1975364/biaya-wajib-seller-tiktok-shop-vs-shopee-siapa-paling-banyak); news reports, not Seller Centre pages).
Our 4% is lower than a marketplace for fee-heavy categories such as fashion, but not lower for low-fee categories (from 2.5%), and it is **about the same as Mayar's** (3% + 0.7% QRIS, no monthly fee).
The pitch is therefore not "cheaper than everyone". It is **pay-per-sale, on a channel the seller owns, with a catalog built from photos and a buyer-protection hold**, and sellers have to value those enough to pay for them.

### Against the tools sellers can buy today

Checked on 2026-10-15 on the vendors' own pages. Prices change; re-check before quoting.

| Product | What it is | Price on its page | What it means for us |
|---|---|---|---|
| [cshub](https://cshub.id/pricing) | AI customer service and order creation inside WhatsApp, payment gateway integration | Starter Rp300,000 / month (1,000 transactions, 20 products); Migration Rp700,000 (the FAQ says Rp500,000); Business Rp2,500,000. The page says prices are estimates | A seller who sells Rp7.5 million a month pays us the same Rp300,000 as Starter (4% commission). Below that we are cheaper, above it dearer. Its page mentions no payout hold or return flow, which does not prove it has none |
| [Jubelio](https://jubelio.com/harga) | Order and inventory system for marketplaces, social commerce and web stores | Rp150 per order, terms apply | A back-office for sellers who already sell on many channels. It makes Jubelio a partner (Connector API), not a competitor for the same seller |
| [Mayar](https://mayar.id/pricing) | Storefront, catalog, checkout, QRIS, courier pickup, a merchant-side MCP server | Starter Rp0 per month, 3% platform fee on physical goods + 0.7% QRIS (before tax); Business Rp349,000 per month, 2% + 0.7%; withdrawal fee Rp2,775 ([fees](https://docs.mayar.id/mor/fees)) | Our 4% is **not a discount** against Mayar (about 3.7%). With a Rp4,000 gateway fee passed on, an Etalase order of Rp170,000 costs about Rp10,800 in total (6.4%: the commission plus the fixed fee). The difference has to come from the catalog built from photos, reserved stock and the hold. We saw no documented hold or public buyer-agent catalog on its pages |
| Meta Business Agent / WhatsApp Business AI | An AI sales agent inside WhatsApp Business | "Free to start, paid later" on Meta's blog; Meta's pricing page says message charges apply from 1 August 2026 (about US$2 per million tokens). Rollout and pricing in Indonesia are unconfirmed | Free conversation help that sellers can switch on in an app they already use. It recommends products from an existing catalog; on the pages we read it does not run QRIS checkout or build the catalog from photos. If it adds payment, our remaining difference is the catalog quality and the hold |
| Shopee (fashion, non-Mall) | Marketplace | 10.0% admin fee + Rp1,250 per completed order, before other service fees (see above) | Our price is lower for this category, but a marketplace brings its own buyers |

The honest comparison is not "cheaper than cshub" or "cheaper than Mayar" at every volume. Etalase's difference is the **catalog built from photos and voice notes**, **reserved stock**, a
**payout hold plus returns** that a chat-bot subscription does not include, and a catalog an agent can search if its owner connects it. Whether sellers value that is exactly what the pilot has to test
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
| WhatsApp Cloud API | ~4 utility templates + ~3–5 charged replies per order (Meta documentation says service messages and in-window utility replies are charged from 1 Oct 2026, **re-open the page before quoting**, see below) × 1,000 orders at ~IDR 357–460 | ~US$160–260 |
| Biteship | Grow plan from 2 Nov 2026: Rp149,000 per month (2,000 shipments, 10,000 rate checks); shipping fees are paid from prepaid Bitepoints or a monthly invoice (needs a business entity) | ~US$9 + prepaid shipping float |
| Xendit xenPlatform account fee | Indonesian price page: Rp25,000 per active sub-account per month; the global English page shows a far higher schedule; **unconfirmed** | ~US$155 for 100 active sellers at the Indonesian price |
| QRIS payments | MDR 0% only if the seller is classified as micro (see above); the fixed gateway fee, if it applies, is shown to the buyer | Pass-through if classified; otherwise 0.7% |
| **Total platform cost (excluding gateway fees and refunds)** | | **≈ US$350–520** |

**Unit economics (our arithmetic from the figures above, an estimate):** commission at 4% on an IDR 170,000 order = IDR 6,800. Per-order costs are WhatsApp IDR 2,535–4,140
(7–9 charged messages at IDR 357–460), AI IDR 90–380, cloud IDR 160–480 and Biteship's subscription spread over 1,000 orders (IDR 149). The **account fee is per seller, not per order**, so the margin depends
on how many orders a seller has:

| Orders per seller per month | Account fee per order | Platform cost per order | Margin against the IDR 6,800 commission |
|---|---|---|---|
| 4 | IDR 6,250 | IDR 9,184–11,399 | −IDR 2,384 to −4,599 |
| 10 (the pilot assumption above) | IDR 2,500 | IDR 5,434–7,649 | +IDR 1,366 to −849 |
| 20 | IDR 1,250 | IDR 4,184–6,399 | +IDR 2,616 to +401 |
| Any (if there is no account fee) | IDR 0 | IDR 2,934–5,149 | +IDR 3,866 to +1,651 |

The table leaves out the IDR 4,000 gateway fee (assumed passed on to the buyer), refund and dispute losses, operator time, support and VAT on the commission. If the IDR 4,000 were absorbed, the margin at 10 orders per seller would be about −IDR 2,600 to −4,800.
So **a pure 4% commission is only comfortable above roughly 20 orders per seller per month**, and we have no data on how many orders a chat-first seller gets. A monthly minimum or a small subscription (cshub's price points suggest the alternative) is a pricing option to test in seller interviews.
AI cost stays small (about 1–6% of the commission): the pressure is on messages and accounts, not on model calls. The design therefore keeps seller messages short and combined, and the per-seller message meter is a priority (see the levers table below).

Price references: [Meta WhatsApp pricing (non-template messages)](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing/non-template-messages),
[Xendit Indonesia fees](https://id.xendit.co/biaya/index.html), [Biteship pricing](https://biteship.com/en/pricing), [Mayar pricing](https://mayar.id/pricing),
[Qontak (Indonesia rates, Oct 2026)](https://help-center.qontak.com/hc/en-us/articles/48430892733465-Latest-WhatsApp-Message-Prices-October-2026),
[SleekFlow rate card](https://help.sleekflow.io/en_US/daftar-harga),
[Bank Indonesia QRIS MDR via Kontan](https://keuangan.kontan.co.id/news/kebijakan-mdr-qris-0-berlaku-mulai-1-oktober-2026-btn-sesuaikan-tarif-merchant),
[Xendit pricing](https://xendit.co/en-id/pricing/).

## WhatsApp cost and other channels

**What changed.** Meta's newer documentation page on non-template messages says that from 1 October 2026 Meta charges for service messages, at the same per-market rate as utility, and that utility messages sent as replies inside the 24-hour window are charged too.
Meta's older pricing page and its WhatsApp Business marketing pages still say replies in the window are free, and they have not been updated. Resellers (Qontak, SleekFlow) list a service price of about Rp357 in Indonesia. A free monthly quota of 1,000 service messages is reported only by resellers and we could not find it in Meta's text.
We read these pages through a summarising fetch tool, so **re-open Meta's page in a browser before quoting it to anyone.** The evidence rows are in [doc 15](15-evidence.md).

**What it costs us** (our arithmetic, an estimate): about 4 utility templates per order at Rp357–460 plus 3–5 charged replies is 7–9 messages, **Rp2,535–4,140 per order**, against a 4% commission of
Rp6,800 on a Rp170,000 order: **37–61% of the commission**. This was the "bad case" in earlier versions of this document; it is now the base case, and the number of replies per order is our assumption.
The design answer does not change but its priority rises: fewer, combined messages, and decisions as buttons.

| Lever | Status |
|---|---|
| Utility templates only for alerts the seller must see (stock check, new paid order, ship now, return) | Built |
| Buyers never receive a WhatsApp message from us: they use the order page (`/pay/{id}`) or their agent | Built |
| One combined reply per photo burst; a per-seller message meter that counts charged messages per order | Proposed, not built; **high priority** because every charged message comes out of the Rp6,800 commission |
| A second seller channel with no window and no fee (Telegram) | Built, optional |
| Dashboard shows what needs attention, so a seller who already looks at it needs no reminder | Built (the buttons stay in WhatsApp) |

**Do we keep WhatsApp?** Yes, as the seller's channel. Sellers are already there (BPS: 95.33% of e-commerce businesses use instant messaging to sell, a definition that includes any business taking orders over the internet), and WhatsApp is the only
one of the Meta channels where an order alert can reliably reach a seller **outside** the 24-hour window, through an approved utility template.

**Click-to-WhatsApp (CTWA) ads and the free window.** A conversation that starts from a CTWA ad opens a free window, but its length is **unresolved**: Meta's page as we read it, Wati and Chatfuel say 72 hours, while Qontak, respond.io and Spurnow (citing a Meta changelog entry of 28 September 2026) say up to 7 days; the Meta changelog copy we could read ends on 12 May 2026 ([doc 15](15-evidence.md)). We do not quote a number. **Unconfirmed.** It matters to us in a narrower way than "use CTWA to be free": Etalase's alerts go to a *seller*, on events (a paid order)
that no ad opens, so a CTWA window does not make them free. It can make the seller's **first chat** with Etalase free if Etalase runs the ad that brings the seller in, and it makes the *buyer* chats
of sellers who run CTWA ads free to those sellers, in their own WhatsApp. So the design answer stays the same: few, combined messages, buttons for decisions, and a second channel that has no per-message fee.

**A second seller channel (built): Telegram.** The `Messenger` interface now has two real adapters. A seller can link a Telegram chat from the dashboard and choose it for orders, stock checks and payouts
([doc 07](07-whatsapp-seller-agent.md#channels-whatsapp-and-telegram), [ADR 0008](adr/0008-seller-channels-and-shop-addresses.md)). A Telegram bot can message a user at any time once the user started it: no
24-hour window, no templates, no per-message fee (Telegram's Bot API pages describe no price). It is the cheapest way to show that the seller side is a channel and that cost can be moved. It is not
claimed to be where Indonesian sellers are: WhatsApp is, and stays the default and the sign-in identity.

**Instagram and Facebook.** What Meta's own pages say (read 2026-10-15 and 2026-10-16, [doc 15](15-evidence.md)): Instagram messaging is for professional accounts, works inside a 24-hour
window, one-time notifications are not available on it, and "some features may not work properly until your app has been granted Advanced Access", which an app serving accounts it does not own needs;
Messenger has the same 24-hour window, with a Human Agent tag and utility templates; neither page states a price. We found no Meta page that says Instagram or Messenger messaging is free, so we do not claim it: **unverified**.
Our reading, **not verified with Meta**: an automatic "new paid order" alert to a seller who has been quiet for a day could not be sent reliably on Instagram, and that is exactly the alert that must not be lost. So:

- **Instagram as an input and a door, not as the alert channel** (built): import product posts as drafts, and put the store link in the bio (the shop address, `rina.<domain>`, is short enough to say aloud).
- **The store link makes Instagram and Facebook sellers reachable now**, because the buyer pays and tracks on our page and the seller still gets the alert on their channel.
- **A buyer-side Instagram DM bot is a different product, and is not built.** Two directions have two rules: seller ↔ Etalase can use one shared number or bot for all sellers (the seller is our customer); buyer ↔ seller
  must run **per seller, on that seller's own account** through the seller's OAuth grant, because a buyer talks to `@toko-rina` and never to an "Etalase" account. It could reuse the agent tools
  (search, product, shipping, checkout) and send the checkout link, since payment cannot happen inside a DM. Against it: customer-service chatbots for WhatsApp and Instagram are a crowded category (cshub, Cekat.ai and others),
  it needs Advanced Access and Business Verification, and it blurs the agent-commerce story. It stays on the roadmap (phase 3), to be piloted after the seller interviews.
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

The larger risks to margin are **WhatsApp messaging** (the biggest cost per order), the **per-seller account and subscription fees** that low-volume sellers cannot absorb, and support, not model inference.

## Tax

See [Security, privacy and compliance → Tax](09-security-privacy-compliance.md#tax). In short:
commission is quoted excluding VAT, sellers handle their own income tax with Etalase's monthly
reports, and marketplace PPh 22 collection (postponed to 31 October 2026 and possibly to 2027) is watched through
the country pack's `tax_profile`. A platform that holds merchant earnings in an escrow account may fall under the PMK 37/2025 definition of a collector; the threshold is not known (see [doc 09](09-security-privacy-compliance.md#tax)).

## Go-to-market

1. **Pilot in one category and city** (fashion / hijab sellers in Jakarta and Bandung) through seller
   communities and WhatsApp groups.
2. **Partners:** payment providers and local website builders via the Connector API.
3. **Distribution through partner app stores** (OMS, marketplace partner programmes) for stock sync.

## Assumptions to validate first

1. Sellers' willingness to pay 3–5% per order, and whether they prefer a commission, a commission with a monthly minimum, or a small subscription.
2. Orders per seller per month (the break-even above is about 20; the pilot plan assumes 10) and the share of sellers who track stock at all.
3. When UCP checkout reaches Indonesia, and whether a QRIS payment handler appears.
4. Payment provider support for sub-accounts and **delayed release** (the hold in [ADR 0007](adr/0007-payout-hold-and-returns.md)) for our use case. We found no Xendit page that documents a hold controlled by the platform: sub-account users withdraw from their own dashboard and, in Indonesia, auto-withdrawal can be changed but not paused or removed; the only "hold" in Xendit's documents is its own reserve. DOKU documents customizable hold periods (not confirmed for QRIS). **This is the gating question for the main differentiator.**
5. Whether sellers accept waiting 3 to 7 days after delivery for their money, in exchange for buyers who trust a store they have never heard of.
6. Cost of a refunded order. Xendit's pages say the payment fee and VAT come back only on a full refund within 24 hours, so a return refund
   usually costs the fee. Who bears it (seller, Etalase, or a share of the commission) is not decided. Refund disputes also cost operator time, which is not in the table above.
7. Return rate in the pilot category. Fashion has high return rates on marketplaces; we have no number for WhatsApp sellers and will not quote one until we measure it.
