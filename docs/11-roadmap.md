# 11. Roadmap

| Phase | Goal | Scope |
|---|---|---|
| **1. Hackathon MVP** (Oct 2026) | Prove the end-to-end flow | WhatsApp catalog from photos and text, optional Instagram import, readiness score, MCP / REST / UCP gateway, demo Gemini buyer agent, stock reservation and confirm-first, QRIS in test mode, Biteship rates, simulated payouts, seller dashboard, Connector API, a public shop with product page and checkout, **Telegram as a second seller channel**, optional `<shop>.<domain>` addresses |
| **2. Indonesia pilot** | 50–100 real sellers | Firestore + Pub/Sub + Cloud Tasks, Google Sheets sync, KYC and payouts via payment sub-accounts (with the delayed release the payout hold needs), automatic Xendit refunds, prepaid return labels, per-buyer return limits, an operator console for disputes, Merchant Center product feed, WhatsApp templates approved, seller onboarding playbook, monthly sales report and commission invoice |
| **3. More channels** | Reach sellers beyond WhatsApp | An **Instagram DM bot for buyers, per seller** (the seller's own account, Advanced Access needed, decide after pilot interviews: [doc 10](10-business-model.md#whatsapp-cost-and-other-channels)); OMS adapters (Jubelio, Ginee), e-commerce plugins (WooCommerce first), website-builder partners via the Connector API, Shopee and TikTok Shop stock sync, seller analytics |
| **4. Regional** | Same core, new markets | Country packs for Malaysia, the Philippines, Thailand and Vietnam; more seller channels (LINE, Zalo, Messenger, Instagram) behind the `Messenger` interface; Stripe where supported; cross-border shipping with duties and HS codes; tax profiles per market |

Dates beyond the hackathon are indicative and depend on the pilot results.

## Gates before real money moves (from the 16 Oct 2026 market review)

Phase 2 does not start until these have answers. None is a code task.

1. **Xendit in writing:** can a platform hold a sub-account balance and release it by API call; what the xenPlatform account fee is for Indonesia; whether the Rp4,000 fixed fee applies; how sub-merchants are classified for QRIS MDR; the current QRIS refund and settlement rules; KYC for sellers without an NIB. (DOKU's custom hold periods and Midtrans are the alternatives to ask in parallel.)
2. **Counsel:** whether instructing releases and deducting a commission is a regulated payment activity (PBI 23/6/PBI/2021), whether Etalase is a PMSE needing a licence (PP 80/2019), the PMK 37/2025 collector definition, the PP 33/2026 controller/processor role, and which legal entity can sign the provider contracts.
3. **Meta or a BSP:** confirm the seller agent is on the "business purpose" side of the WhatsApp Business AI policy, and the real price of in-window replies in Indonesia.
4. **Ten seller conversations** ([doc 17](17-market-fit-and-direction.md#validation-plan-ten-seller-conversations)): orders per month, COD share, who pays for returns, commission versus minimum fee versus subscription, 72/168-hour hold, and whether a catalog built from their own photos is accepted with few corrections. Also try Meta's business AI, cshub and Mayar as a seller would.
5. **Extraction quality with Gemini** on real catalogs ([doc 12](12-benchmark-plan.md)); the only measurement so far is the offline baseline.

Product changes from the market review, status on 2026-10-16:

- **Built:** `Order.channel` and a coarse referrer label with an operator report; Biteship `return_in_transit` and `disposed` as their own statuses (a returned or lost parcel refunds the buyer, a returning parcel moves nothing) with a six-hour recheck for lost webhooks; the published payout policy at `/policy/payouts`; the QRIS-only decision ([ADR 0009](adr/0009-qris-only-no-cod.md)).
- **Not built, awaiting a decision:** one combined reply per photo burst with a per-seller message meter. Every charged message comes out of the commission, so until it is built the WhatsApp cost per order stays an assumption (7–9 messages) and not a measurement.
- **Not done, external:** the question to Meta or a BSP above. The appeal path on the policy page needs `SUPPORT_CONTACT` set and a person to answer it.

## What could change the order

- UCP checkout arriving in Indonesia earlier → prioritise Merchant Center feed and UCP compliance.
- Strong demand from website sellers → move the Connector API partnerships and WooCommerce plugin up.
- Payment provider limits on sub-accounts → adjust the payout design before scaling the pilot. If the provider cannot keep a sub-account balance unreleased for the return window, the hold has to move into a provider-side feature (scheduled disbursement) or be dropped for a smaller deposit model; decide with the provider's answer in hand ([ADR 0007](adr/0007-payout-hold-and-returns.md)).
