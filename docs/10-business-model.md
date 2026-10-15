# 10. Business model and cost

## Pricing

- **Commission per delivered order, 3–5%** (4% in the prototype), excluding VAT.
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
| WhatsApp Cloud API | ~4 utility messages per order × 1,000 orders at ~IDR 366–460 (free inside an open service window) | ~US$90–115 |
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
4. Payment provider support for sub-accounts and delayed release for our use case.
