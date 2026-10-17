# Trying Etalase (for reviewers)

You do not need an account, a WhatsApp number or an API key. The sandbox address is in the submission form; it ends in **`/demo`**.

The sandbox is the real application running with three things simulated, so that you can play the seller, the buyer and an AI agent in one browser: the WhatsApp chat, the QRIS payment and the courier. The table below says exactly what is real and what is not.

## Five minutes, step by step

Open `/demo`. You are playing a seller (a made-up shop, "Toko Demo Rina"). The left side is her WhatsApp chat.

1. **Build a catalog.** Tap the **Catalog** suggestion (it fills in a description and picks a sample photo), then **Send**. Etalase replies with a draft: product, colours, stock, price, an agent-readiness score. Tap **Publish** in the chat.
2. **Buy as a person.** Open the shop link under *Open and try*. The shop already holds three seeded products plus the one you just published. Pick one and check out (any made-up name, phone number and address), or tap **Add to cart** on two products and check out the cart: several products of one shop become one order, one QRIS code and one shipment. An agent can do the same with `items` over MCP, REST and UCP.
3. **Pay.** The order page shows a QRIS code. The **Simulate the QRIS payment** button under it stands in for scanning with a banking app.
4. **Run the order as the seller.** The chat now shows *New paid order*. Tap **Accept**, then send `tracking JNE123456`. Under **Orders**, tap **Courier delivers**, then **Skip the return window**: the payout is released (4% commission, the rest to the seller). Before you skip the window, open the order page as the buyer and try **Ask for a return** (it asks for the phone number you ordered with) to see the other branch.
5. **Buy as an AI agent.** Point any MCP client at the **MCP address** shown on the page, or read the **UCP profile** (`/.well-known/ucp`). An agent can search, check availability and the seller's return policy, and check out; its QRIS page links back to the same demo payment button. From a terminal, `python -m etalase.agents.buyer_agent "black voal hijab under Rp100k to Bandung 40135"` (needs a Gemini key) does it with an agent we wrote.
6. **Two more things to try.** Buy the *Silk Pashmina in Cream*: only two are left, so the seller is first asked *"Is it still available?"* in the chat and the buyer waits for her answer. And **Seller dashboard** → *Sign in as the demo seller* shows the seller's own view (orders, products, return policy, shop address).

## What is real and what is simulated

| | In this sandbox | Beyond it |
|---|---|---|
| Catalog from text and photos | Real code. With a Gemini key on the server it is Gemini (the page says which); without one, rules parse the typed text and photos are not read | Accuracy on real seller photos is not measured: see `prototype/evals` |
| Store page, product page, checkout, order page | Real | |
| Stock reservation and the "confirm with the seller first" rule | Real (tested under concurrency) | |
| MCP server, REST agent API, UCP checkout and profile | Real. UCP: in the official conformance suite 30 of 77 tests pass, 21 fail and 26 are skipped ([doc 16](docs/16-ucp.md)) | |
| Payout hold, return window, return flow, refunds | Real logic; the **clock is simulated**: a button skips the wait | |
| WhatsApp | **Simulated.** The "phone" is a web page reading the app's message queue. The WhatsApp Cloud API adapter is written and unit-tested, but has never been run against live Meta traffic | Needs a Meta business setup |
| QRIS payment | **Simulated.** A mock payment provider and the button above. The Xendit adapter is written and unit-tested, never run live | Needs a Xendit account |
| Courier | **Simulated.** Mock rates and a "delivered" button. The Biteship adapter is written and unit-tested, never run live | Needs a Biteship account |
| Seller sign-up in chat, and a shop on its own subdomain with its own `/.well-known/ucp` | **Not in the sandbox.** There is one shared demo seller, already verified, and its shop is at `/stores/<id>`: the address the sandbox runs on has no wildcard DNS, so no `<shop>.` addresses exist. Both features are built in the application (off by default) and are shown in the demo video, run locally. Sign-up is left out on purpose: with the mock payment provider a new shop verifies at once, so a public sign-up would let anyone put a live page on our address with nobody checking. Design: [doc 02](docs/02-product.md), [ADR 0010](docs/adr/0010-chat-signup-address-first.md) | A live run needs a domain with wildcard DNS and a certificate, and someone to verify each shop. The payment provider's own identity check is not built; an operator does it by hand |
| Photos | Three bundled sample images only. There is no upload: the sandbox is public and shared, and we would not host strangers' pictures | |

## Rules of the sandbox

- **One shared seller.** Everything you do is visible to the next visitor, and theirs to you. If a message in the chat is not yours, someone else is testing at the same time.
- **Use made-up details.** Orders placed in the sandbox, including the buyer's name, phone and address, can be seen by every other visitor (in the seller dashboard, for one). Nothing is sent anywhere, but do not type real personal data, and the same goes for an agent you point at it.
- **Products you publish disappear from the shop after an hour.** The seeded ones stay, and their stock is topped up automatically.
- **It resets when the server restarts**, which is why the sandbox keeps everything in memory. Nothing you type is stored anywhere else.
- **Nothing real is behind it.** The server refuses to start in this mode if a payment, courier or WhatsApp key is present, and refuses it outside the development setting, and refuses it with chat sign-up switched on (`DEMO_MODE`, `prototype/etalase/security.py`).
- Requests are rate-limited per address. If you see *slow down*, wait a minute.

## Run it yourself

```bash
cd prototype
make setup          # once: virtualenv, dependencies, tests
make judge          # http://localhost:8080/demo
make e2e-judge      # the steps above over real HTTP, starts its own server
make browser-judge  # the same in headless Chromium (needs Playwright)
```

To deploy your own copy to Cloud Run as a separate service (it never touches a real deployment, and takes only the Gemini key from `.env`):

```bash
cd prototype
DEMO=1 PROJECT_ID=my-project make deploy
```

## What this does not prove

It proves our own wiring end to end. It does not prove the live integrations, that real sellers will use WhatsApp this way, that they will pay for it, or how accurate Gemini is on real seller photos. [Known gaps](learn/master/known-gaps.md) lists these, and [Evidence and sources](docs/15-evidence.md) says where each market number comes from.
