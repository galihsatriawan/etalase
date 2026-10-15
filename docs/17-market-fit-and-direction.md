# 17. Market fit and direction

Written on 2026-10-15 after re-checking the market and competitor claims behind the idea. Every fact below is labelled
**verified** (read on the source's own page on that date), **partly** (read, with a caveat) or **unverified**. Sources and quotes are in
[doc 15](15-evidence.md). This is not legal or investment advice.

## The question

Not "does the idea fit our code", but: **is the idea still relevant to the Indonesian market and to what competitors already offer?**

## Short answer

Yes, but narrower than the first pitch, and with a different centre of gravity.

- The problem is real and large: Indonesian online sellers work in chat apps, with catalogs in photos and voice notes (BPS 2023, verified).
- Tools already exist for the seller's own human customers (cshub, Jubelio, others). We did not find one on its own pages that offers an
  **agent-facing storefront** (MCP, UCP) built from a seller's photos. That is our difference, and it is only valuable if agents or
  agent-like channels send buyers. Today they barely do in Indonesia.
- So the value we can **prove today** is on the seller side (catalog from photos, stock, orders, QRIS, couriers, payouts) and on **trust**
  (payout hold, returns, a visible policy). The agent side is an option on the future, not the revenue plan.

## What is verified, partly verified, unverified

| Topic | Fact | Status |
|---|---|---|
| Channels | 95.33% of Indonesian e-commerce businesses sell through instant messaging, 33.29% on social media, 17.80% on marketplaces, 1.77% on websites (BPS, E-Commerce Statistics 2023, via Databoks). Businesses use several channels | **Verified** (secondary pages; the PDF table was not opened) |
| WhatsApp today | GoodStats (26 Mar 2026) names WhatsApp the most used social platform for selling in 2026 | **Partly** (headline page, numbers not read) |
| Marketplace share | A GoodStats × Jakpat survey (Feb 2026, 371 business owners) and an INDEF survey list marketplaces but not WhatsApp, so they cannot size chat commerce | **Partly**; earlier percentages we quoted from them were wrong and are withdrawn |
| UCP in Indonesia | Google named Indonesia as a future market in January 2026 (InfoQ). Google's May 2026 post and the October 2026 Merchant Center news do not mention Indonesia. Checkout is US-only on Google's own pages; Australia started with select retailers in June (Ragtrader) and the self-serve hub is expected there in 2027 (PPC Land, 7 Oct 2026) | **Verified**; no date for Indonesia is known |
| Agent buyers | Forecasts exist for the US (Morgan Stanley); we found no figure for Indonesian consumers buying through agents | **Unverified** for Indonesia |
| cshub | AI customer service that creates orders inside WhatsApp; Starter Rp300,000 a month; no mention of agent storefronts, payout holds or returns on the page | **Verified** as a statement about the page |
| Jubelio | Order and stock system, Rp150 per order, many channels | **Verified** as a statement about the page |
| Qontak, Mayar, Ginee, Sirclo | Prices and features from earlier research | **Unverified**, not to be quoted |
| Seller willingness to pay 3–5% and to wait 3–7 days for the payout | No data. We have not spoken to sellers | **Unverified** and the largest gap |
| Return rates of WhatsApp sellers | No data | **Unverified** |
| Xendit holding a seller balance for the hold; automatic refunds | Xendit's own pages disagree on QRIS refunds (7 vs 30 days, partial or not) | **Unverified** |

## Does Etalase need UCP in Indonesia to be useful?

No. UCP is one door. The same catalog and checkout are open through MCP and REST to any agent, and the seller side works without any agent.

That was the real gap in "usable now": a buyer could only arrive through an agent (MCP, REST or UCP), more than UCP timing. **It is now closed**: every
seller has a public store link (`/stores/{id}`) where a person sees the catalog and the return policy, buys, pays with QRIS and follows the order, and agents read the
same page. See [doc 02](02-product.md#2b-buying-without-an-agent-the-store-link) and [doc 09](09-security-privacy-compliance.md). Whether anyone uses it is still unmeasured.

## What changed in the product because of these facts

Built in this iteration (see [ADR 0007](adr/0007-payout-hold-and-returns.md) and [doc 05](05-checkout-payment-payout.md#payout-hold-and-returns)):

1. **Payout hold.** Money is released after delivery plus a 72-hour window (168 hours for a seller with fewer than 5 completed orders).
   Why: an agent cannot inspect goods and we do not verify sellers, so a refund must be possible before the seller is paid.
2. **Returns as a flow with owners and timers** (agent tool, seller buttons, operator queue), not a support ticket. Why: "what if there are many returns?" was the first question every reviewer asked.
3. **A courier-returned parcel now refunds the buyer** instead of leaving the order stuck. Found while building the flow.
4. **The return policy and a track record are shown to the buyer's agent** (MCP, UCP `refund_policy` link, schema.org, a public page). Why: this is the honest
   substitute for KYC, which is not built.
5. **Commission is charged only when the payout is released.** A refunded order earns nothing.
6. **Claims corrected.** "UCP live in Australia" is now "started with select retailers"; withdrawn the unsupported percentages.

Not changed, on purpose: one core with MCP, REST and UCP doors; WhatsApp as the seller's only required app; QRIS and couriers through providers.

## Direction: what is built and what needs a decision

| Item | Why | Cost | Risk if skipped |
|---|---|---|---|
| **Public store link** for buyers (people and agents on the same page), shareable in an Instagram bio or a WhatsApp status; orders still land in WhatsApp | Closes the real gap: usable without an agent | **Built** (2026-10-15): catalog, buy form, QRIS page, order status, return form; server-rendered HTML; found and fixed a form-post bug in a real browser | Demand depends on agents that barely exist in Indonesia; people-demand is unmeasured |
| **One combined WhatsApp reply per photo burst**, plus a usage meter | Cost: each extra WhatsApp message and Gemini call comes out of a 4% commission | Small | Margin disappears for chatty sellers |
| **`Order.channel`** (`mcp`, `rest`, `ucp`, later `web`) | Lets us measure which door sends buyers; needed for any claim about demand | Small | We cannot show where orders came from |
| **Seller interviews** (below) | Every willingness-to-pay and return-rate assumption is unverified | Time, no code | The largest risk stays unmeasured |
| Automatic Xendit refunds and provider-side delayed release | The hold is only as real as the provider's support for it | Depends on Xendit's answer | Operators send every refund by hand |
| Real seller KYC | The payout hold reduces buyer risk, it does not identify sellers | Provider onboarding | Trust rests on policy and track record only |
| **A second seller channel** (Telegram) behind the `Messenger` interface | Shows the seller side is a channel, not a WhatsApp feature, and moves alerts off a per-message price | **Built** (2026-10-16), [ADR 0008](adr/0008-seller-channels-and-shop-addresses.md) | The "channel adapter" story stays a claim |
| **Shop addresses** (`rina.<domain>`) | A link a seller can say aloud and put in a bio; one origin per shop | **Built**, optional (`STORE_BASE_DOMAIN`) | Links look like a platform, not a shop |
| **Buyer-side Instagram DM bot**, per seller | Where Indonesian sellers talk to buyers; no WhatsApp window problem on the seller's own account | **Not built**, roadmap | See the next section |

## Channels, cost and addresses (2026-10-16)

Questions raised after the first UI review, and where they landed. Facts are labelled as in the table at the top; sources are in [doc 15](15-evidence.md).

| Question | Answer |
|---|---|
| Can the seller side handle more than WhatsApp (Telegram, Instagram, Facebook, LINE)? | Yes in design, **one proven**: orders, stock checks, payouts and the dashboard code reach a linked Telegram chat through the same interface, and an end-to-end test shows one order flow on both. The others each need their own review and window rules: not built |
| Is "use Click-to-WhatsApp ads to be free" right? | **Unconfirmed**, and narrower for us than it sounds. Two resellers describe a free window for conversations that start from a CTWA ad (7 days vs 72 hours, they disagree); Meta's own page that we could read still showed the old rules. CTWA frees a business's chat with someone who clicked an ad. Etalase's alerts go to sellers on events no ad opens, so it does not make them free. It can make a seller's first chat free (our own ads) and the buyer chats of sellers who run CTWA ads |
| Would an Instagram DM bot make Etalase stronger? | For market fit, partly: it is where sellers talk to buyers and a seller's own Instagram has no per-message price we could find (**unverified**). It must run **per seller, on the seller's own account**: no buyer buys from an "Etalase" account. It would reuse the agent tools and send the checkout link. For the competition it is weaker per unit of effort: chatbots for WhatsApp and Instagram are a crowded category, the agent-commerce story gets blurred, and it needs Advanced Access (App Review), so a live demo against real sellers' accounts is unlikely before the deadline. **Roadmap, not built** |
| Can each shop have a subdomain? | Yes, **built** and optional: `rina.<domain>`, needs wildcard DNS and a wildcard certificate. Each shop gets its own browser origin. Per-shop UCP profiles are not built |

Two directions have two rules: seller ↔ Etalase can use one shared bot or number for every seller; buyer ↔ seller must be per tenant.

## Risks (a second opinion, and our view)

| Risk raised | Our view |
|---|---|
| Too broad: WhatsApp, Instagram, UCP, MCP, payments, couriers at once | Agreed. The pitch should be one story: a WhatsApp seller sends photos, an agent buys, the order and money behave. Everything else is a footnote |
| "Retail and commerce" is a crowded theme | Agreed. The difference is the seller who has no store system, and the money rules (hold, returns) that most demos skip |
| UCP is not live in Indonesia | Verified, and we say so. MCP works today; UCP readiness is a bonus |
| WhatsApp message cost | Real: about half of the platform cost per order ([doc 10](10-business-model.md)). Fix: fewer, combined messages |
| Gemini must be central | It is the catalog builder and the seller agent. The demo should show a messy input (mixed language, voice note) turning into a clean catalog |
| A second opinion called the idea "potential but not an automatic favourite" | Fair. Our edge is depth in one flow, not breadth |

## How to describe it honestly

Say: "Sellers in Indonesia work in WhatsApp. Etalase turns their photos into a catalog any AI agent can read and buy from, keeps the order in WhatsApp,
holds the payout until the buyer's return window ends, and shows agents the seller's return policy."

Do not say: "UCP is live in Indonesia", "agents are already buying from Indonesian sellers", a return rate, a willingness-to-pay figure, "Xendit holds the funds for us",
"sellers are KYC-verified", or any market-share number that [doc 15](15-evidence.md) marks unverified.

## Validation plan: ten seller conversations

Goal: replace the unverified rows above with answers. Ten WhatsApp or Instagram sellers in one category (fashion or hijab), in Jakarta or Bandung.

1. Where do your orders come from this month, and how many? How do buyers pay and ship today?
2. How often does a buyer ask to return or complain? What do you do? Show me the last time.
3. Would you accept being paid 3 days after delivery (7 for the first five orders) if buyers trusted you more? What would you want instead?
4. Would you pay 4% per order? What do you pay now (marketplace, a chat tool, nothing)?
5. Do you keep stock numbers anywhere? How do you know something is sold out?
6. Have buyers ever used an AI assistant to ask about your products? Would you want that?
7. Show us your catalog as it exists (photos, captions, notes). Can we build the shop from it live?

Signals that the idea holds: most sellers describe returns as a real, recurring problem; at least some accept the hold; sellers accept the catalog
produced from their own photos with few corrections. Signals that it does not: sellers see no returns problem and refuse any delay, or would use only
the catalog and ignore agents. Either result changes the plan, which is why it is the next step.

## Sources

Evidence rows and quotes: [doc 15](15-evidence.md). Key pages read on 2026-10-15:
[cshub pricing](https://cshub.id/pricing), [Jubelio pricing](https://jubelio.com/harga),
[Google blog, 20 May 2026](https://blog.google/products-and-platforms/products/shopping/shopping-updates-google-marketing-live),
[PPC Land, 7 Oct 2026](https://ppc.land/canada-and-australia-face-2027-wait-for-googles-merchant-center-ucp-hub/),
[Xendit help: QR refunds](https://help.xendit.co/hc/en-us/articles/17209624920345-How-to-refund-a-QR-Code-transaction-using-API),
[Xendit docs: QR refunds](https://docs.xendit.co/qr-codes/payment-flows/refund),
[schema.org MerchantReturnPolicy](https://schema.org/MerchantReturnPolicy).
