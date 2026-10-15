# 14. AI Builder Cup 2026 alignment

Theme: **Retail & Commerce: Intelligent Customer and Business Experiences**, which explicitly
covers discovery, conversational shopping and inventory.

## Judging criteria

| Criterion | Weight | How Etalase addresses it |
|---|---|---|
| Technical Merit & Gen AI Implementation | 40% | Gemini multimodal extraction with structured output (photos + voice + text → catalog); ADK seller agent with tenant-scoped tools; ADK buyer agent over MCP; MCP server and UCP-style discovery; transactional stock reservation (tested under concurrency); event-driven design on Cloud Run |
| Problem Alignment & Impact | 25% | Brings chat-commerce sellers (95% of Indonesian online businesses sell through chat apps, BPS 2023) into AI-agent shopping; pay-per-order pricing below marketplace fees; payouts after delivery |
| Innovation & Creativity | 25% | Agent-commerce layer for sellers without a store system; WhatsApp as the seller interface; confirm-first mode for uncertain stock; Connector API so builders and OMSs enable all their stores at once |
| User Experience & Solution Design | 10% | No new app for sellers; buttons for decisions; bilingual messages; honest availability for agents; simple dashboard with WhatsApp sign-in |

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
