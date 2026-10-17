# 1. Problem and market

## The shift: AI agents are becoming a shopping channel

In 2026 the big AI platforms started to put shopping inside the assistant, but real checkout is still narrow and mostly outside Indonesia:

- **Google** launched the **Universal Commerce Protocol (UCP)** at NRF in January 2026, letting shoppers buy inside AI Mode in Search and the Gemini app. UCP supports REST, Agent2Agent (A2A) and the Model Context Protocol (MCP), and works with the **Agent Payments Protocol (AP2)** as its payment layer. Merchants publish a JSON profile at `/.well-known/ucp`.
- Agent checkout through UCP is live in the **US** (February 2026). In **Australia** it started in June 2026 with select retailers such as Kogan and The Iconic (the first Asia-Pacific market), but Google's own pages still say the checkout is US-only: on 20 May it said Canada and Australia would follow "in the coming months" ([Google blog](https://blog.google/products-and-platforms/products/shopping/shopping-updates-google-marketing-live)), and on 7 October the self-serve Merchant Center UCP hub was US-only, with Australia and Canada expected in 2027 ([PPC Land](https://ppc.land/canada-and-australia-face-2027-wait-for-googles-merchant-center-ucp-hub/)). Google is testing Flipkart checkout in Gemini in **India**. One trade report (InfoQ, January 2026) says UCP will expand to markets including Indonesia, but Google's own January announcement names only "eligible U.S. retailers", and its May and October 2026 statements do not mention Indonesia, so **no date for Indonesia is known**. Onboarding is by Google's selection and needs a payment provider that can process Google Pay tokens. A Google VP has said it plans to work with APAC partners such as Flipkart and Shopee.
- **Alibaba** said its Qwen AI app handled more than 120 million consumer orders in six days (6–11 February 2026) during a subsidised Lunar New Year campaign.
- **OpenAI scaled back ChatGPT Instant Checkout in March 2026** and refocused on product discovery, after reports that users were not using ChatGPT as a place to buy ([Storyboard18](https://storyboard18.com/digital/openai-reduces-chatgpt-shopping-focus-shifts-to-product-discovery-93192.html)). Google (US, selected merchants), Microsoft and Perplexity (US) run checkout inside chat; Anthropic has published a commerce blueprint that leaves payment to the builder.
- **In Indonesia, AI discovery is live and agent checkout is not.** Google AI Mode has supported Indonesian since September 2025, and Google's AI shopping for Indonesia launched on 16 March 2026 (as we read Google's post: starting in English and pointing shoppers to where they can buy, with no UCP checkout). Shopee in ChatGPT (June 2026) sends the shopper on to Shopee to pay, and Lazada's Lazzie and Grab's shopping agent work inside their own platforms (Grab's stops at the cart). We found **no report of an agent-initiated purchase in Indonesia**, and Visa's Agentic Ready programme (April 2026) covers ten Asia-Pacific markets without Indonesia.
- Forecasts are US-only and vendor-driven: Morgan Stanley expects agents to handle about 10% (up to 20%) of US online retail by 2030. Agent demand in Indonesia is unmeasured, so we treat the agent door as an option, not a revenue plan.

Agents do not browse storefronts like people. They request **structured catalog data** (title, description, price, availability, variants) through feeds or MCP servers. A shop without that data is invisible to them.

## The region: commerce in Southeast Asia runs through chat

- Southeast Asia's e-commerce GMV reached about **US$185 billion in 2025**, video commerce is about a
  quarter of GMV, and consumer interest in AI is about **3× the global average**
  (e-Conomy SEA 2025 by Google, Temasek and Bain).
- Chat is a normal way to buy in the region. The only regional comparison we found is a 2019 Facebook × BCG study (funded by Facebook). One press summary reports 47% social media or chat, 44% e-commerce platforms and 9% brand sites for the region, another says "close to half"; **29% of Indonesian respondents** had made a conversational-commerce purchase, against 40% in Thailand and 36% in Vietnam. For Indonesia we rely on the BPS figures below, not on this study.
- Each market has its own chat app (WhatsApp in Indonesia and Malaysia, LINE in Thailand, Zalo in
  Vietnam, Messenger in the Philippines), which is why Etalase keeps messaging behind an adapter.
- Morgan Stanley estimates AI shopping agents could handle **US$190–385 billion of US online spending by 2030** (about 10%, up to 20% in an optimistic case). This is a US scenario, not an Indonesian or regional one.

## The problem: the sellers who need it most are not ready

- Only **11% of SMB merchants** are agent-ready, versus 19% of large merchants, and only **15%** of merchants have structured product data agents can use (PYMNTS × Visa, 2026 Global Digital Shopping Index).
- In Indonesia, our first market, BPS (*E-Commerce Statistics 2023*, 40,011 businesses, multiple answers allowed) found that **95.33% of e-commerce businesses use instant messaging apps** such as WhatsApp to sell, 33.29% social media, **17.80% marketplaces** and 1.77% their own website. "E-commerce business" here means a business that took orders or sold over the internet, so a food stall that takes orders on WhatsApp counts. The figure shows that chat is the default order inbox. It does **not** show that 95% of sellers lack a store system, and BPS does not give the overlap between chat and marketplace sellers. The share of product sellers who only use chat has not been measured. The 2024 edition (about 4.4 million businesses) was published in November 2025 and we have not yet read its channel table.
- These sellers keep their catalog in **photos, captions, voice notes and spreadsheets**. No agent can read that.
- Marketplace fees keep rising: in 2026 Shopee and TikTok Shop revised seller fees upward (for example a 10% admin fee for fashion for non-Mall sellers on Shopee Indonesia, and a higher cap on TikTok Shop's dynamic commission from 18 May 2026). Many sellers want a channel of their own, but we have not yet interviewed any, so this is a hypothesis.

**Etalase** turns what these sellers already have into a catalog and a store link that ordinary shoppers can pay by QRIS, and that an AI agent can search and reserve from if its owner connects it to our MCP server. The seller keeps working in WhatsApp (or Telegram). The seller side and the store link are the product today; the agent door is an option for later.

## Competitive landscape

| Player | What they do | Gap for our segment |
|---|---|---|
| The New Era of Shopping (Era) — US$1.4M pre-seed, Feb 2026 | Makes brand catalogs discoverable and shoppable in AI assistants | Enterprise and established brands |
| ShopAgentic (Germany) — €1.9M, Jun 2026 | Prepares retailer systems for AI agents | Retailers with existing commerce stacks |
| AgentReady (Shopify app), Shopify Catalog & Agentic Storefronts | Audit and syndicate catalogs to AI surfaces (Shopify's Agentic plan is open to non-Shopify brands since March 2026) | Built for brands with structured catalogs and card or wallet checkout; not for chat sellers without a store, no QRIS or WhatsApp flow |
| Stripe Agentic Commerce Suite | Agent checkout for WooCommerce, Wix, Squarespace, BigCommerce | Stripe merchants; Stripe Indonesia is invite-only and lacks QRIS |
| Shopee (Shopee app in ChatGPT in 8 markets incl. Indonesia, Jun 2026; agentic shopping prototype with Google) | Brings Shopee listings to AI agents | Keeps sellers inside the marketplace and its fees |
| [Mayar](https://mayar.id/pricing) (pages read 15–16 Oct 2026; scale is a vendor claim) | Storefront, product catalog, checkout, QRIS and courier pickup; 3% platform fee on physical goods plus 0.7% QRIS on the free plan; a merchant-side MCP server with 65 tools ([mcp.mayar.id](https://mcp.mayar.id)) | The closest fast follower. We did not see a public, stock-aware catalog for buyer agents, UCP, intake from chat photos, or a documented payout hold |
| **Meta Business Agent / WhatsApp Business AI** ([Meta](https://whatsappbusiness.com/blog/introducing-meta-business-agent-ai/)) | An AI sales agent inside WhatsApp Business that answers customers, recommends products from the seller's existing catalog and "closes sales"; reported by the press as launched for Indonesian businesses on 11 Aug 2026 (rollout, pricing and payment support in Indonesia are unconfirmed) | Strongest threat on the conversation side. On the pages we read it does not build a catalog from photos, run QRIS checkout, or expose the catalog to third-party agents. Meta could add any of these |
| [cshub](https://cshub.id/pricing) (page checked 15 Oct 2026) | AI that creates orders from WhatsApp chats; payment links, a stock-aware catalog, Biteship couriers and Midtrans QRIS; plans from Rp300,000 a month (its pricing page contradicts itself on one plan) | Same chat-to-order-to-pay path with the same courier layer, so the WhatsApp + QRIS + Biteship combination is not a moat. Its pages mention no agent-facing catalog (MCP, UCP) and no payout hold or returns, which does not prove it has none |
| [DOKU MCP server](https://developers.doku.com/accept-payments/doku-mcp-server) | A payments MCP server with 30+ tools including a QRIS tool; "still early" per DOKU (26 May 2026) | Etalase is not the only MCP bridge to QRIS. DOKU is a possible alternative rail or partner, not a storefront |
| Cekat.ai, Lincah (which acquired Orderfaz in Nov 2025), Qontak and other WhatsApp tools | AI chat agents, COD and courier aggregation, and CRM for businesses | Serve human buyers, not AI agents. Catalog, payment and courier steps are not visible on Cekat's pages (vendor scale claims not independently checked) |
| [Jubelio](https://jubelio.com/harga) (page checked 15 Oct 2026) | Order, stock and warehouse system across marketplaces, social commerce and web stores; Rp150 per order | A back-office for sellers who already run many channels; WhatsApp is only a contact link. A partner for the Connector API, not a replacement for it |

**Our position:** a catalog, store link and order flow for chat-first sellers (they take their orders in chat and have no store page or checkout of their own to give a buyer or an agent; some also list on a marketplace), starting with WhatsApp sellers in Indonesia, with local payments (QRIS) and couriers, and agent access (MCP, and UCP when a market opens) as an option on the same data. Across competitors we did not find a tool that builds the catalog from photos and voice notes, but that rests on marketing pages and a limited search, not on testing their products.

## Why won't Shopee or Meta just do this?

- **Marketplaces bring their own listings to AI agents**, with discovery leading back to their own
  checkout and fees. That is the right move for them, and it leaves out sellers who work in chats and
  are not on a marketplace (the BPS figures show how common chat is, not how many sellers have no
  marketplace listing at all).
- **Meta is moving into the conversation, for free.** Meta Business Agent already answers buyers in
  WhatsApp Business and recommends products from the seller's catalog. In Indonesia WhatsApp's own
  payment feature (December 2025) only copies bank details, and Meta states that it does not process payments.
  On the pages we read, Meta's agent does not build a catalog from photos, run QRIS checkout, reserve
  stock or open the catalog to third-party agents. **If Meta adds payment or catalog intake, our
  remaining difference is the catalog-from-photos quality, stock reservation and the payout hold.**
- **Etalase is neutral and open:** one catalog the seller owns, reachable by any agent whose owner
  connects it to our MCP server and, when a market opens, through UCP, with orders landing back in the
  seller's chat. If a platform opens up, Etalase plugs into it through an adapter rather than
  competing with it. We do not claim that agents can discover the server on their own: no source shows
  consumer assistants finding sellers' MCP servers automatically.

## Why we can win

1. **Input we found no one else handling:** catalogs built from photos, voice notes and chats with Gemini. On 43 labelled text messages Gemini 3.8 Flash got 42 fully right (98%) after a prompt fix made on those same messages; before the fix it got 36 (84%), and offline rules 21 (49%). Our target is 90%. Photos, voice notes and the number of corrections real sellers need are not measured (see [doc 12](12-benchmark-plan.md)).
2. **Output where sellers already work:** orders, confirmations and stock updates in WhatsApp.
3. **Local rails:** QRIS and e-wallets through Xendit, 30+ couriers through Biteship.
4. **Built for many markets:** one core with country packs, plus a Connector API for OMSs and website builders.

## Risks we are tracking

- **Timing.** UCP checkout is not live in Indonesia and no date is known; Google's UCP payment handlers are card-based (Google Pay), not QRIS. Mitigation: Etalase does not need UCP to work. The seller side (catalog from photos, stock, orders in WhatsApp, QRIS, couriers, payouts, returns) runs in the prototype, and a buyer's agent that its owner connects to our MCP server can search and reserve now, with a human paying the QRIS link. The public store link ([doc 17](17-market-fit-and-direction.md)) is the buyer entry point for people who do not use an agent.
- **Volume.** We found no measured agent-initiated purchase in Indonesia, OpenAI scaled back checkout in March 2026, and survey evidence says shoppers accept AI for searching and comparing but not for deciding and paying. Mitigation: pay-per-order pricing, so sellers carry no risk while volume grows, and the store link works without any agent. We should also log where store-link visits come from (for example chatgpt.com or gemini.google.com), because that is the one agent-demand signal we can measure ourselves.
- **Unit economics.** Fixed costs per seller (WhatsApp messages, a possible payment-platform account fee, a courier subscription) can exceed the 4% commission when a seller has few orders. See [doc 10](10-business-model.md).
- **Trust.** A buyer's agent cannot inspect goods, and we do not verify sellers ourselves. Mitigation: the payout is held through a return window, returns are a built flow, and agents see each seller's return policy and track record ([ADR 0007](adr/0007-payout-hold-and-returns.md)). Real seller KYC is not built.
- **Fast followers.** The nearest are **Mayar** (storefront, QRIS, couriers and a merchant-side MCP server already exist) and **cshub** (stock-aware catalog, AI orders, Biteship and Midtrans QRIS); Meta is closing in from the conversation side. Mitigation: catalog quality from photos, stock reservation, the payout hold and returns, seller network, partnerships through the Connector API. None of these is proven yet.

## Sources

- Full evidence with quotes and dates: [15. Evidence and sources](15-evidence.md)
- Indonesia sales channels: [BPS E-Commerce Statistics 2023](https://www.bps.go.id/en/publication/2025/01/30/d52af11843aee401403ecfa6/statistik-e-commerce-2023.html), [Databoks summary](https://databoks.katadata.co.id/en/technology-telecommunications/statistics/67a5866310206/few-local-e-commerce-players-sell-on-marketplaces), [GoodStats](https://goodstats.id/article/mayoritas-usaha-e-commerce-berjualan-lewat-aplikasi-pesan-instan-HmFph)
- Region: [e-Conomy SEA 2025 (Bain press release)](https://www.bain.com/about/media-center/press-releases/sea/e-conomy-sea-2025/),
  [Temasek](https://www.temasek.com.sg/en/news-and-resources/news-room/news/2025/e-conomy-sea-2025-report-aseans-digital-economy-poised-to-surpass-300-billion),
  [Facebook × BCG conversational commerce study (KrASIA)](https://kr-asia.com/shoppers-in-southeast-asia-prefer-to-buy-using-chat-or-voice-applications-according-to-joint-study)
- Agentic commerce forecast: [Morgan Stanley Research](https://morganstanley.com/insights/articles/agentic-commerce-market-impact-outlook)

- Google UCP: [Google Developers Blog](https://developers.googleblog.com/en/under-the-hood-universal-commerce-protocol-ucp/), [UCP specification](https://ucp.dev/2026-04-08/specification/overview/), [eCommerceNews Asia](https://ecommercenews.asia/story/google-unveils-open-ucp-standard-for-ai-driven-shopping), [Retail Brew (US)](https://www.retailbrew.com/stories/2026/02/11/google-makes-etsy-and-wayfair-items-shoppable-within-agentic-ai-search), [Ragtrader (Australia)](https://www.ragtrader.com.au/news/the-iconic-kogan-test-google-s-agentic-ai-checkout), [Storyboard18 (India plan)](https://storyboard18.com/digital/google-and-flipkart-to-launch-universal-commerce-platform-soon-91935.html), [TechCrunch (India test)](https://techcrunch.com/?p=3170226), [InfoQ (next markets)](https://infoq.com/news/2026/01/google-agentic-commerce-ucp)
- Alibaba Qwen orders: [SCMP](https://scmp.com/tech/article/3343289/alibabas-qwen-tops-120-million-orders-6-days-amid-chinas-ai-shopping-battle), [KrASIA](https://kr-asia.com/alibabas-latest-qwen-subsidies-are-a-bet-on-agentic-commerce)
- Merchant readiness: [PYMNTS × Visa](https://www.pymnts.com/smbs/2026/visa-says-small-merchants-could-move-faster-on-ai-shopping/)
- Shopee fees: [Metro TV News](https://www.metrotvnews.com/read/NxGCP3en-shopee-kerek-biaya-admin-mulai-2026-segini-nominalnya)
- Competitors: [Seedtable (Era)](https://seedtable.com/companies/the-new-era-of-shopping/funding-rounds/pre-seed-2026-02), [Tech.eu (ShopAgentic)](https://funding.tech.eu/companies/B571889D-814C-4305-BFA9-4B56B12706C5), [AgentReady](https://pickyourapp.com/products/agentready-4), [Addigitech (Shopify Catalog)](https://addigitech.com/blog/agentic-commerce-shopify), [Shopify](https://shopify.com/news/agentic-commerce-momentum), [Sea (Shopee in ChatGPT)](https://www.sea.com/news/406), [FashionNetwork (Google × Sea)](https://uk.fashionnetwork.com/news/Google-shopee-owner-sea-to-develop-ai-tools-for-e-commerce,1808880.html)
- Stripe in Indonesia: [Stripe Support](https://support.stripe.com/questions/supported-payment-methods-currencies-and-businesses-for-stripe-accounts-in-indonesia)
