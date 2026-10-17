# 14. AI Builder Cup 2026 alignment

Theme: **Retail & Commerce: Intelligent Customer and Business Experiences**, which explicitly
covers discovery, conversational shopping and inventory.

## Judging criteria

| Criterion | Weight | How Etalase addresses it |
|---|---|---|
| Technical Merit & Gen AI Implementation | 40% | Gemini multimodal extraction with structured output (photos + voice + text → catalog); ADK seller agent with tenant-scoped tools; ADK buyer agent over MCP; MCP server; UCP business server (profile and Checkout over REST, run against the official conformance suite); transactional stock reservation (tested under concurrency); event-driven design on Cloud Run |
| Problem Alignment & Impact | 25% | Brings chat-first sellers into online shopping (BPS 2023: 95.33% of Indonesian e-commerce businesses use instant messaging to sell, multiple answers allowed; the share who sell only in chat is not measured) with a catalog built from photos, a QRIS store link and an agent door; pay-per-order pricing below the marketplace fee for fee-heavy categories such as fashion and about equal to Mayar; payouts after delivery and the return window. Unproven so far: willingness to pay, orders per seller, and whether the payout hold can be enforced at the provider |
| Innovation & Creativity | 25% | Catalog from photos and voice notes plus an agent-readable store for sellers without a store system (we found no competitor page that claims the first part); WhatsApp as the seller interface, with a channel adapter (Telegram built as proof, [ADR 0008](adr/0008-seller-channels-and-shop-addresses.md)); confirm-first mode for uncertain stock; Connector API so builders and OMSs enable all their stores at once |
| User Experience & Solution Design | 10% | No new app for sellers (WhatsApp, or Telegram if they prefer); buttons for decisions; bilingual seller messages; honest availability for agents; a buyer shop that behaves like a shopping app (store, product page with option chips, one-screen checkout, a QR code that can be saved, search and sort, smooth in-place navigation, no-JavaScript fallback, reduced-motion support, checked in a real browser by `scripts/browser_check.py`); a dashboard for a phone: tappable status tiles, orders that say what is waiting on whom, shop address and channel settings. Buyer pages are English only; Indonesian copy is not done |

## Google Cloud AI usage

| Google technology | Where |
|---|---|
| Gemini (Vertex AI) | Catalog extraction from photos, voice notes and text (`catalog/extractor.py`); seller and buyer agents |
| Agent Development Kit (ADK) | `agents/seller_agent.py`, `agents/buyer_agent.py` (`McpToolset`) |
| Cloud Run | Hosts the API, MCP server, webhooks and dashboard |
| Firestore | Transactional catalog, stock and orders (`store/firestore.py`) |
| Pub/Sub, Cloud Tasks | Events and reservation expiry (pilot) |
| Secret Manager | Keys and tokens |
| Universal Commerce Protocol / AP2 | Discovery profile and roadmap alignment |

## Deliverables checklist

- [ ] Team of 2–4 registered (solo participation is not allowed; registration closes 11 October)
- [ ] Deck converted to PDF (`submission/`)
- [ ] Working prototype deployed on Cloud Run, end to end
- [ ] Reviewer sandbox deployed (`DEMO=1 make deploy`), link in the submission, steps in [JUDGES.md](../JUDGES.md): reviewers can run the flow without an account because WhatsApp, payment and courier are simulated there
- [ ] Public GitHub repository (this repo, created fresh during the hackathon)
- [ ] Demo video, about 3 minutes, public YouTube / Vimeo / Drive link
- [ ] Everything in English (code, docs, deck)

Sources: [FAQ](https://aibuildercup.com/Faqs.html), [Themes](https://aibuildercup.com/themes.html),
[Rewards](https://aibuildercup.com/rewards.html).
