# 11. Roadmap

| Phase | Goal | Scope |
|---|---|---|
| **1. Hackathon MVP** (Oct 2026) | Prove the end-to-end flow | WhatsApp catalog from photos and text, optional Instagram import, readiness score, MCP / REST / UCP-style gateway, demo Gemini buyer agent, stock reservation and confirm-first, QRIS in test mode, Biteship rates, simulated payouts, seller dashboard, Connector API |
| **2. Indonesia pilot** | 50–100 real sellers | Firestore + Pub/Sub + Cloud Tasks, Google Sheets sync, KYC and payouts via payment sub-accounts, Merchant Center product feed, WhatsApp templates approved, seller onboarding playbook, monthly sales report and commission invoice |
| **3. More channels** | Reach sellers beyond WhatsApp | OMS adapters (Jubelio, Ginee), e-commerce plugins (WooCommerce first), website-builder partners via the Connector API, Shopee and TikTok Shop stock sync, seller analytics |
| **4. Regional** | Same core, new markets | Country packs for Malaysia, the Philippines, Thailand and Vietnam; chat adapters for LINE, Zalo and Messenger; Stripe where supported; cross-border shipping with duties and HS codes; tax profiles per market |

Dates beyond the hackathon are indicative and depend on the pilot results.

## What could change the order

- UCP checkout arriving in Indonesia earlier → prioritise Merchant Center feed and UCP compliance.
- Strong demand from website sellers → move the Connector API partnerships and WooCommerce plugin up.
- Payment provider limits on sub-accounts → adjust the payout design before scaling the pilot.
