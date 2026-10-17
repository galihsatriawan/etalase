# How to use this guide

Etalase Academy takes you from "I designed this with an AI assistant" to "I can build, demo, defend and run every part of it", one level at a time.

::: tip What you will learn
- What Etalase is, in 60 seconds, so every later page has a frame to hang on
- How the six levels are organised and what each one gives you
- A 7-day study plan, and a 1-evening fast track for when the deadline is close
- How to run this site locally
- How to read the status badges (Built, Mocked in demo, Roadmap) that appear on every page
:::

## Who this guide is for

This guide is written for the person who has to stand in front of the AI Builder Cup 2026 judges and answer any question about Etalase, and then run it as a startup. It assumes:

- You are comfortable with Python and TypeScript, HTTP APIs, JSON and Git.
- You are **new** to agentic commerce, payments (QRIS, payment sub-accounts, refunds) and the WhatsApp Business Cloud API.
- You want to know what the code *actually* does, not what the pitch deck hopes it does.

Every page follows the same shape: why it matters, how it works (with a diagram), a tour of the real code with file names, the pitfalls, and a short self-check at the end. Code excerpts are copied from the repository; when a page names a file such as **`prototype/etalase/checkout/service.py`**, you can open it and find the same lines.

## Etalase in 60 seconds

AI assistants have started to buy things for people (Google's Universal Commerce Protocol, AP2 and the Model Context Protocol), but they can only see shops that publish **structured catalogs**. Most small sellers in Indonesia sell through WhatsApp chats, with their "catalog" living in product photos, captions and voice notes, so AI shopping agents cannot see them at all. Etalase fixes that. A seller sends photos and a short description (typed or as a voice note) to Etalase's WhatsApp number; Gemini turns them into products with variants, prices, weights and an agent-readiness score; the seller taps **Publish**. From then on any AI agent can find the products through Etalase's **MCP server**, get courier prices and check out. Stock is reserved while the buyer pays with **QRIS**; the order arrives in the seller's WhatsApp with **Accept** / **Out of stock** buttons; the seller replies with a tracking number; and the payout (order value minus a 4% commission in the prototype) is released only after the courier confirms delivery. When stock is uncertain, a **confirm-first** mode asks the seller "Is it still available?" before the buyer is asked to pay.

## The six levels

The sidebar is split into six levels. Each one builds on the one before.

| Level | Pages | What it gives you |
|---|---|---|
| **Start here** | This page | The map, the study plan and the badge legend |
| **1 · Foundations** | [The problem](/foundations/problem), [Agentic commerce 101](/foundations/agentic-commerce), [The Etalase product](/foundations/product), [Glossary](/foundations/glossary) | The *why* and the *what*: the sellers, the market, the protocols (MCP, UCP, AP2, A2A), the product journeys end to end |
| **2 · Run it** | [Run the demo locally](/run/run-locally), [Hands-on walkthrough](/run/hands-on) | The prototype running on your laptop with no credentials, and a guided tour you can repeat before the demo |
| **3 · How it works** | [Architecture overview](/architecture/overview) through [Security model](/architecture/security) | The code, module by module: data model, catalog and stock, checkout, the agent gateway, the WhatsApp seller agent, integrations, security |
| **4 · Ship it** | [Testing](/ship/testing), [Deploy to Cloud Run](/ship/deploy), [Turn on real services](/ship/real-services) | The 731 tests, the Cloud Run deploy, and how to switch Gemini, WhatsApp, Xendit and Biteship from mock to real |
| **5 · Business and pitch** | [Business model and costs](/business/business-model), [Evidence and sources](/business/evidence), [Pitch and Q&A playbook](/business/pitch-and-qa) | Pricing, unit economics, every statistic with its source, and answers to likely judge questions |
| **6 · Mastery** | [Exercises](/master/exercises), [Known gaps](/master/known-gaps), [Final quiz](/master/quiz) | Changes you make yourself, the honest list of what is not done, and a test of the whole thing |

If you only remember one rule: **Level 1 tells you what to say, Level 3 tells you why it is true, Level 6 tells you where it is not true yet.**

## Status badges

Etalase is a hackathon prototype. Some parts run for real, some run against mocks in the demo, and some are designed but not written. Every page marks this with a badge so you never claim more than the code does.

| Badge | Meaning | Examples |
|---|---|---|
| <Badge type="tip" text="Built" /> | The code exists and runs in the prototype | MCP server, stock reservations, confirm-first, readiness score, WhatsApp one-time-code sign-in, Connector API, Instagram import from sample posts |
| <Badge type="warning" text="Mocked in demo" /> | The real adapter is written and switches on with credentials, but the offline demo uses a stand-in | Gemini extraction and the ADK seller agent (offline parsers without a key), Xendit QRIS (`MockPayment`), Biteship (`MockShipping`), WhatsApp Cloud API (console outbox), Firestore (`MemoryStore`) |
| <Badge type="info" text="Roadmap" /> | Designed in the docs, not implemented | Pub/Sub, Cloud Tasks, Cloud Storage, Cloud KMS, Google Sheets sync, OMS adapters, WooCommerce plugin, AP2 |

::: warning Mocked is not the same as built
"Mocked in demo" means the real integration has code, but it has not been exercised end to end in the demo. For example, `XenditPayment.refund` and `release_to_seller` deliberately raise `ManualActionRequired` until the account setup is confirmed (see **`prototype/etalase/adapters/payment.py`**). Say "wired, not live" when a judge asks.
:::

## Suggested 7-day study plan

About two hours a day. Each day ends with something you can *do*, not just read.

| Day | Read | Do |
|---|---|---|
| 1 | [The problem](/foundations/problem), [Agentic commerce 101](/foundations/agentic-commerce) | Explain to a friend, without slides, why a WhatsApp seller is invisible to an AI agent |
| 2 | [The Etalase product](/foundations/product), [Glossary](/foundations/glossary), [Run the demo locally](/run/run-locally) | Run `python scripts/demo.py` and match each of its ten steps to the product journeys |
| 3 | [Hands-on walkthrough](/run/hands-on), [Architecture overview](/architecture/overview), [Data model and store](/architecture/data-model) | Start the server, call `/agent/v1/search` and `/.well-known/ucp` by hand |
| 4 | [Catalog and stock](/architecture/catalog-and-stock), [Checkout and orders](/architecture/checkout-and-orders) | Draw the order state machine from memory, then check it against `checkout/service.py` |
| 5 | [Agent gateway](/architecture/agent-gateway), [WhatsApp seller agent](/architecture/seller-agent), [Integrations](/architecture/integrations), [Security model](/architecture/security) | Trace one MCP `create_checkout` call from the tool to the store |
| 6 | [Testing](/ship/testing), [Deploy to Cloud Run](/ship/deploy), [Turn on real services](/ship/real-services) | Run `pytest -q` (731 tests); deploy once; turn on Gemini |
| 7 | [Business model](/business/business-model), [Evidence](/business/evidence), [Pitch and Q&A](/business/pitch-and-qa), [Known gaps](/master/known-gaps), [Final quiz](/master/quiz) | Rehearse the 3-minute demo and answer the ten likely judge questions out loud |

## Fast track: one evening

The hackathon deadline is close (team registration closes 11 October, per `docs/14-hackathon-alignment.md`). If you have only one evening, do this in order, about three hours in total:

1. **20 min.** Read "Etalase in 60 seconds" above, then [The Etalase product](/foundations/product). Focus on the sequence diagram and the Built / Mocked / Roadmap table.
2. **20 min.** Run the prototype and read its output line by line:

   ```bash
   cd prototype
   pip install -r requirements-dev.txt
   python scripts/demo.py
   pytest -q
   ```

3. **30 min.** Skim [Agentic commerce 101](/foundations/agentic-commerce): MCP and UCP are the parts judges probe most (then read [UCP checkout](/architecture/ucp)).
4. **30 min.** [Architecture overview](/architecture/overview) and [Checkout and orders](/architecture/checkout-and-orders): reservations, confirm-first and "payout only after the courier confirms delivery".
5. **40 min.** [Evidence and sources](/business/evidence) and [Pitch and Q&A playbook](/business/pitch-and-qa). Memorise the four headline numbers and where they come from.
6. **20 min.** [Known gaps](/master/known-gaps). Knowing your weaknesses is what makes an answer sound honest.

## Running this site

The site is a VitePress project in the `learn/` folder of the repository.

```bash
cd learn
npm install
npm run dev        # http://localhost:5173
```

`npm run build` produces a static site in `learn/.vitepress/dist`. To publish on GitHub Pages under a repository path, build with `BASE=/etalase/ npm run build` (see `learn/README.md`). Search is local (top bar), and Mermaid diagrams render in the browser.

## How to read the code alongside

Keep the repository open in an editor next to the site. The prototype lives in `prototype/etalase/`, with one folder per area: `catalog/`, `checkout/`, `gateway/`, `seller/`, `agents/`, `adapters/`, `store/`, `connector/` and `api/`. The design documents in `docs/` (numbered 01 to 15) and the decision records in `docs/adr/` are the "why" behind the code; the Academy pages link the two together and point out where they disagree.

## Check yourself

1. What are the three status badges, and which one applies to Xendit QRIS in the demo?

::: details Answer
Built, Mocked in demo and Roadmap. Xendit QRIS is **Mocked in demo**: `XenditPayment` is written and switches on with `XENDIT_SECRET_KEY` and `XENDIT_CALLBACK_TOKEN`, but the offline demo uses `MockPayment`, and refunds and payouts through Xendit are still manual steps.
:::

2. In one sentence, why can't an AI shopping agent buy from a typical WhatsApp seller today?

::: details Answer
Agents read structured catalog data (title, price, availability, variants) through feeds or protocols like MCP, and a WhatsApp seller's catalog only exists as photos, captions and chat messages.
:::

3. Which level would you open to answer "what happens if two agents buy the last unit at the same time?"

::: details Answer
Level 3, [Catalog and stock](/architecture/catalog-and-stock) and [Checkout and orders](/architecture/checkout-and-orders). The reservation is atomic, and the demo and tests race many buyers for the last unit: exactly one wins.
:::

4. Is Google Sheets sync something you can demo?

::: details Answer
No. It is **Roadmap** (designed in `docs/08-integrations.md`, not implemented). What you can demo as optional catalog sources are the Instagram import (sample posts offline) and CSV text through the offline extractor.
:::

## Next

Start Level 1 with [The problem](/foundations/problem).
