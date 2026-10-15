# 1. Problem and market

## The shift: AI agents are becoming a shopping channel

In 2026 the major AI assistants started completing purchases for people:

- **Google** launched the **Universal Commerce Protocol (UCP)** at NRF in January 2026, letting shoppers buy inside AI Mode in Search and the Gemini app. UCP supports REST, Agent2Agent (A2A) and the Model Context Protocol (MCP), and works with the **Agent Payments Protocol (AP2)** as its payment layer. Merchants publish a JSON profile at `/.well-known/ucp`.
- Agent checkout through UCP is live in the **US** (February 2026). In **Australia** it started in June 2026 with select retailers such as Kogan and The Iconic (the first Asia-Pacific market), but Google's own pages still say the checkout is US-only: on 20 May it said Canada and Australia would follow "in the coming months" ([Google blog](https://blog.google/products-and-platforms/products/shopping/shopping-updates-google-marketing-live)), and on 7 October the self-serve Merchant Center UCP hub was US-only, with Australia and Canada expected in 2027 ([PPC Land](https://ppc.land/canada-and-australia-face-2027-wait-for-googles-merchant-center-ucp-hub/)). Google is testing Flipkart checkout in Gemini in **India**. Indonesia was named as a future market in January 2026 ([InfoQ](https://infoq.com/news/2026/01/google-agentic-commerce-ucp)) but is not named in Google's May or October 2026 statements, so **no date for Indonesia is known**. A Google VP has said it plans to work with APAC partners such as Shopee.
- **Alibaba** said its Qwen AI app handled more than 120 million consumer orders in six days (6–11 February 2026) during a subsidised Lunar New Year campaign.
- Agent-driven volume is still small, but Morgan Stanley expects agents to handle about 10% (up to 20%) of US online retail by 2030, so the window to prepare is now.

Agents do not browse storefronts like people. They request **structured catalog data** (title, description, price, availability, variants) through feeds or MCP servers. A shop without that data is invisible to them.

## The region: commerce in Southeast Asia runs through chat

- Southeast Asia's e-commerce GMV reached about **US$185 billion in 2025**, video commerce is about a
  quarter of GMV, and consumer interest in AI is about **3× the global average**
  (e-Conomy SEA 2025 by Google, Temasek and Bain).
- Chat is a normal way to buy here: in a Facebook × BCG study, **nearly half** of Southeast Asian
  respondents had bought or sold through social media or messaging apps, and over 40% of Thai and 36% of
  Vietnamese respondents had made a conversational-commerce purchase (Indonesia 29%), versus 5% in the
  US (2019 data, the most recent regional comparison we found).
- Each market has its own chat app (WhatsApp in Indonesia and Malaysia, LINE in Thailand, Zalo in
  Vietnam, Messenger in the Philippines), which is why Etalase keeps messaging behind an adapter.
- Globally, Morgan Stanley estimates AI shopping agents could account for **US$190–385 billion of US
  online spending by 2030** (about 10%, up to 20% in an optimistic case).

## The problem: the sellers who need it most are not ready

- Only **11% of SMB merchants** are agent-ready, versus 19% of large merchants, and only **15%** of merchants have structured product data agents can use (PYMNTS × Visa, 2026 Global Digital Shopping Index).
- In Indonesia, our first market, **95% of e-commerce businesses sell through instant messaging apps** such as WhatsApp, 33% through social media, only **18% through marketplaces** and 2% through their own website (BPS, *E-Commerce Statistics 2023*; businesses can use several channels). Discovery on TikTok or Instagram usually ends in a WhatsApp chat before money moves.
- These sellers keep their catalog in **photos, captions, voice notes and spreadsheets**. No agent can read that.
- Marketplace fees keep rising: in 2026 Shopee and TikTok Shop revised seller fees upward (for example a 10% admin fee for fashion for non-Mall sellers on Shopee Indonesia, and a higher cap on TikTok Shop's dynamic commission from 18 May 2026). Sellers want a channel of their own.

**Etalase** turns what these sellers already have into a storefront AI agents can find and buy from, and keeps the seller working in WhatsApp.

## Competitive landscape

| Player | What they do | Gap for our segment |
|---|---|---|
| The New Era of Shopping (Era) — US$1.4M pre-seed, Feb 2026 | Makes brand catalogs discoverable and shoppable in AI assistants | Enterprise and established brands |
| ShopAgentic (Germany) — €1.9M, Jun 2026 | Prepares retailer systems for AI agents | Retailers with existing commerce stacks |
| AgentReady (Shopify app), Shopify Catalog & Agentic Storefronts | Audit and syndicate catalogs to AI surfaces (Shopify's Agentic plan is open to non-Shopify brands since March 2026) | Built for brands with structured catalogs and card or wallet checkout; not for chat sellers without a store, no QRIS or WhatsApp flow |
| Stripe Agentic Commerce Suite | Agent checkout for WooCommerce, Wix, Squarespace, BigCommerce | Stripe merchants; Stripe Indonesia is invite-only and lacks QRIS |
| Shopee (Shopee app in ChatGPT in 8 markets incl. Indonesia, Jun 2026; agentic shopping prototype with Google) | Brings Shopee listings to AI agents | Keeps sellers inside the marketplace and its fees |
| [cshub](https://cshub.id/pricing) (page checked 15 Oct 2026) | AI customer service that creates orders inside WhatsApp; plans from Rp300,000 a month | Serves the seller's own human customers in chat; its page mentions no agent-facing storefront (MCP, UCP) and no payout hold or returns, which does not prove it has none |
| [Jubelio](https://jubelio.com/harga) (page checked 15 Oct 2026) | Order, stock and warehouse system across marketplaces, social commerce and web stores; Rp150 per order | A back-office for sellers who already run many channels. A partner for the Connector API, not a replacement for it |
| Cekat.ai, Orderfaz and other WhatsApp tools | Chatbots and order tools for WhatsApp sellers | Serve human buyers, not AI agents (not re-checked on their own pages) |

**Our position:** the agent-commerce layer for sellers who have no store system at all, across Southeast Asia's chat-commerce markets, starting with WhatsApp and Instagram sellers in Indonesia, with local payments (QRIS) and couriers.

## Why won't Shopee or Meta just do this?

- **Marketplaces bring their own listings to AI agents**, with discovery leading back to their own
  checkout and fees. That is the right move for them, and it leaves out the 95% of online businesses
  that sell in chats, most of whom are not on a marketplace at all.
- **Meta owns the chat, not the catalog or the checkout.** WhatsApp's catalog feature is for human
  buyers; agents need structured, stock-aware data and a payment flow with local rails (QRIS) and
  couriers, which is outside Meta's business.
- **Etalase is neutral and open:** one catalog the seller owns, exposed to any agent through open
  protocols (UCP, MCP), with orders landing back in WhatsApp. If a platform opens up, Etalase plugs
  into it through an adapter rather than competing with it.

## Why we can win

1. **Input nobody else handles:** catalogs built from photos, voice notes and chats with Gemini.
2. **Output where sellers already work:** orders, confirmations and stock updates in WhatsApp.
3. **Local rails:** QRIS and e-wallets through Xendit, 30+ couriers through Biteship.
4. **Built for many markets:** one core with country packs, plus a Connector API for OMSs and website builders.

## Risks we are tracking

- **Timing.** UCP checkout is not live in Indonesia and no date is known. Mitigation: Etalase does not need UCP to work. The seller side (catalog from photos, stock, orders in WhatsApp, QRIS, couriers, payouts, returns) runs today, and MCP lets any agent buy now. What is still missing is a buyer entry point for people who do not use an agent (a public store link, see [doc 17](17-market-fit-and-direction.md)).
- **Volume.** Agent-driven transactions are still small. Mitigation: pay-per-order pricing, so sellers carry no risk while volume grows.
- **Trust.** A buyer's agent cannot inspect goods, and we do not verify sellers ourselves. Mitigation: the payout is held through a return window, returns are a built flow, and agents see each seller's return policy and track record ([ADR 0007](adr/0007-payout-hold-and-returns.md)). Real seller KYC is not built.
- **Fast followers.** Local WhatsApp-tool vendors could add similar features. Mitigation: seller network, local catalog data, partnerships through the Connector API.

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
