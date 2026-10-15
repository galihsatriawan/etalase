# Etalase

**Make every WhatsApp seller shoppable by AI agents.**

Etalase turns a small seller's product photos and chats into a storefront that AI shopping agents
(Gemini or any MCP client; Etalase also answers Google's Universal Commerce Protocol checkout) can find, check stock on and buy from, with every order landing
back in the seller's WhatsApp.

Built for the **AI Builder Cup 2026** (Google Cloud, JAPAC), theme **Retail & Commerce: Intelligent
Customer and Business Experiences**.

![Architecture](docs/assets/architecture.png)

## Why

- AI assistants started buying for people in 2026 (Google's Universal Commerce Protocol, AP2, MCP).
  Agents only see **structured catalogs**.
- Southeast Asia's e-commerce reached about **US$185B in 2025**, and buying through chat apps is
  normal here (WhatsApp, LINE, Zalo, Messenger). In Indonesia, **95% of online businesses sell through
  chat apps and only 18% use marketplaces** (BPS, 2023 data).
- Only **11% of small and mid-size merchants** are agent-ready, and small sellers keep their catalogs
  in photos, captions and chats, which agents cannot read.
- Marketplaces bring sellers to AI agents only inside their own ecosystem and fees. Sellers want a
  channel of their own.

Every number has a source and a quote in [Evidence and sources](docs/15-evidence.md).

More in [Problem and market](docs/01-problem-and-market.md).

## How it works

1. **Seller sends photos and a voice note on WhatsApp** (or optionally connects Instagram or a
   spreadsheet). Gemini builds products, variants, prices and weights, and scores how agent-ready they
   are. The seller taps **Publish**.
2. **A buyer asks their AI agent.** The agent calls Etalase's **MCP server**: search, shipping
   options, checkout. Stock is reserved while the buyer pays with **QRIS**.
3. **The seller gets the order in WhatsApp** with **Accept** / **Out of stock** buttons, replies
   with a tracking number, and is paid out after delivery, minus a small commission.
4. Uncertain stock? **Confirm-first mode** asks the seller before the buyer pays.
5. OMSs, e-commerce plugins and website builders connect through an **OpenAPI Connector API**.

![Process flow](docs/assets/process-flow.png)

## Run the prototype (no credentials needed)

```bash
cd prototype
pip install -r requirements-dev.txt
python scripts/demo.py          # full end-to-end story in the terminal
pytest -q                       # 217 tests: oversell race, payments, webhook security, OTP abuse
uvicorn etalase.api.main:app --port 8080   # API, MCP at /mcp/, dashboard at /, docs at /docs
```

Without keys, Etalase uses offline parsers, a console WhatsApp outbox and mock payment and
shipping. Add a Gemini key (or Vertex AI), WhatsApp Cloud API, Xendit and Biteship credentials to
switch each part to the real service. See [prototype/README.md](prototype/README.md).

**Only the keys are left to you:** [SETUP.md](SETUP.md) lists every value and where to get it. `make setup` prepares
everything, `make doctor` tests each key against the real service, `make eval` measures extraction accuracy,
`make deploy` ships to Cloud Run from your `.env`, and `make ucp-conformance` runs Google's official UCP conformance
suite against Etalase (run them from `prototype/`).

## Google Cloud AI usage

| Technology | Used for | Status |
|---|---|---|
| **Gemini on Vertex AI** | Multimodal catalog extraction (photos + voice + text) with structured output; seller and buyer agents | Built; switches on with Vertex AI or an API key (offline parser otherwise) |
| **Agent Development Kit (ADK)** | WhatsApp seller agent with tenant-scoped tools; demo buyer agent using `McpToolset` | Built |
| **Cloud Run** | API, MCP server, webhooks, seller dashboard | Built (deploy script) |
| **Secret Manager** | Session secret, dev and webhook tokens, provider keys | Built (deploy script) |
| **Firestore** | Transactional catalog, stock reservations and orders | Built; demo runs on the in-memory store |
| **Pub/Sub, Cloud Tasks, Cloud Storage, Cloud KMS** | Events, per-order expiry, media, token encryption | Pilot (in-process equivalents today) |
| **MCP / UCP / AP2** | MCP server; UCP business server (profile at `/.well-known/ucp`, Checkout over REST); AP2 payments | MCP and UCP built (official conformance suite: see [docs/16](docs/16-ucp.md)); AP2 roadmap |

## Judging criteria at a glance

| Criterion | Where to look |
|---|---|
| Technical Merit & Gen AI (40%) | [Architecture](docs/03-architecture.md), [Catalog & stock](docs/04-catalog-and-stock.md), [Seller agent](docs/07-whatsapp-seller-agent.md), `prototype/` |
| Problem Alignment & Impact (25%) | [Problem & market](docs/01-problem-and-market.md), [Business model](docs/10-business-model.md) |
| Innovation & Creativity (25%) | [Product](docs/02-product.md), [Integrations](docs/08-integrations.md) |
| User Experience & Solution Design (10%) | [Product](docs/02-product.md), [Wireframes](docs/assets/wireframes.png) |

Full mapping: [Hackathon alignment](docs/14-hackathon-alignment.md).

## Learn the project: Etalase Academy

A VitePress site in [`learn/`](learn/) teaches the whole project from zero to master: agentic
commerce basics, a code tour, security, deployment, every number with its source
([evidence page](learn/business/evidence.md)), a judge Q&A playbook, exercises and a quiz.

```bash
cd learn && npm install && npm run dev     # http://localhost:5173
```

It is published to GitHub Pages by `.github/workflows/learn.yml` (Settings → Pages → Source: GitHub Actions).

## Repository map

```
.
├── README.md
├── docs/                         plan, design and decisions
│   ├── 01-problem-and-market.md
│   ├── 02-product.md
│   ├── 03-architecture.md        diagrams: components, sequence, data model
│   ├── 04-catalog-and-stock.md
│   ├── 05-checkout-payment-payout.md
│   ├── 06-shipping.md
│   ├── 07-whatsapp-seller-agent.md
│   ├── 08-integrations.md        Sheets, OMS, e-commerce, Connector API, marketplaces
│   ├── 09-security-privacy-compliance.md   PDP Law, payments, tax
│   ├── 10-business-model.md
│   ├── 11-roadmap.md
│   ├── 12-benchmark-plan.md
│   ├── 13-demo-script.md
│   ├── 14-hackathon-alignment.md
│   ├── 15-evidence.md            every number with its source and quote
│   ├── adr/                      architecture decision records
│   └── assets/
├── learn/                        Etalase Academy (VitePress learning site)
├── prototype/                    Python · FastAPI · ADK · MCP · Cloud Run
│   ├── etalase/
│   ├── scripts/                  demo.py, deploy_cloud_run.sh
│   ├── tests/
│   └── Dockerfile
└── submission/                   deck (.pptx and .pdf)
```

## Status

Hackathon prototype. Working end to end locally with mocks; live integrations (Gemini, WhatsApp
Cloud API, Xendit QRIS, Biteship rates) are wired and switch on with credentials. Known gaps before a
pilot are listed in [Security, privacy and compliance](docs/09-security-privacy-compliance.md#known-gaps-before-a-pilot).

## Team

- [Team member 1]: [role]
- [Team member 2]: [role]

## License

[MIT](LICENSE)
