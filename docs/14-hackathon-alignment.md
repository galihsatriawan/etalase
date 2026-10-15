# 14. AI Builder Cup 2026 alignment

Theme: **Retail & Commerce: Intelligent Customer and Business Experiences**, which explicitly
covers discovery, conversational shopping and inventory.

## Judging criteria

| Criterion | Weight | How Etalase addresses it |
|---|---|---|
| Technical Merit & Gen AI Implementation | 40% | Gemini multimodal extraction with structured output (photos + voice + text → catalog); ADK seller agent with tenant-scoped tools; ADK buyer agent over MCP; MCP server; UCP business server (profile and Checkout over REST, run against the official conformance suite); transactional stock reservation (tested under concurrency); event-driven design on Cloud Run |
| Problem Alignment & Impact | 25% | Brings chat-commerce sellers (95% of Indonesian online businesses sell through chat apps, BPS 2023) into AI-agent shopping; pay-per-order pricing below marketplace fees; payouts after delivery |
| Innovation & Creativity | 25% | Agent-commerce layer for sellers without a store system; WhatsApp as the seller interface, with a channel adapter (Telegram built as proof, [ADR 0008](adr/0008-seller-channels-and-shop-addresses.md)); confirm-first mode for uncertain stock; Connector API so builders and OMSs enable all their stores at once |
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
- [ ] Public GitHub repository (this repo, created fresh during the hackathon)
- [ ] Demo video, about 3 minutes, public YouTube / Vimeo / Drive link
- [ ] Everything in English (code, docs, deck)

Sources: [FAQ](https://aibuildercup.com/Faqs.html), [Themes](https://aibuildercup.com/themes.html),
[Rewards](https://aibuildercup.com/rewards.html).
