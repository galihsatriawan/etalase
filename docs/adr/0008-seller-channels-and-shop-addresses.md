# ADR 0008: Seller channels, and addresses for shops

**Status:** accepted (2026-10-16)

## Context
Two questions came up after the first UI and market review.

1. **Channel cost and reach.** About half of the platform cost per order is WhatsApp messaging ([doc 10](../10-business-model.md)), and Meta's October 2026 pricing is not
   confirmed from Meta's own pages ([doc 17](../17-market-fit-and-direction.md)). The `Messenger` interface existed, but only WhatsApp implemented it, so "the seller side is a channel,
   not a WhatsApp feature" was a claim, not a demonstration.
2. **A link a seller can say aloud.** `/stores/sel_1a2b3c4d5e` on a shared host is hard to read out, hard to remember and looks like a platform, not a shop.

## Decision
**Channels.** Every seller message is sent to an **address** that names its channel: a WhatsApp number as before, or `tg:<chat id>` for Telegram. `Seller.address` returns the
address of the channel the seller chose (WhatsApp by default; `Seller.channel`, `Seller.telegram_chat_id`). A `ChannelRouter` implements `Messenger` and sends each call through the
right adapter, so the checkout, notification and inbox code did not change except to say `seller.address` instead of `seller.whatsapp`. Replies to something the seller wrote
go back through the channel it came from; messages Etalase starts go to the chosen channel.

WhatsApp stays the **identity**: sign-up and dashboard sign-in use the WhatsApp number. A seller links Telegram from the dashboard (a signed 10-minute code in a deep link,
`t.me/<bot>?start=<code>`) and can switch back at any time.

**Addresses.** With `STORE_BASE_DOMAIN` set, a shop may claim a slug and is served at `https://<slug>.<domain>/`. A Host-header middleware maps `/` on a shop host to that shop's
store page; every other path keeps working, and a host nobody claimed answers 404 (so wildcard DNS never exposes the dashboard). Unset, nothing changes.

**A shop address is also that shop's agent surface.** UCP describes a business by the profile on its own domain, so `rina.<domain>/.well-known/ucp` is Rina's profile: the same capabilities,
and an endpoint that is Rina's address. The middleware resolves the shop once and puts it in the request state; `/ucp/v1`, `/agent/v1` and `/mcp` on that address then see only Rina's products and
orders, and the links in their answers (`continue_url`, the return-policy link, the store page's "for AI shopping assistants" block) point back at Rina's address. A UCP checkout session belongs to the
shop it was started on: another shop's address answers 404 for it, and an idempotency key is scoped to the shop. `/stores/<another shop's id>` on Rina's address is a 404, so an address never shows
another shop's pages under its name. On the platform host nothing changes: every shop is reachable there, as before.

## Why Telegram first
A bot can write to a user at any time once the user has started it: no 24-hour window, no templates, no per-message fee. It is the cheapest honest demonstration that one order can
reach a seller on another channel, and the seller side has no regulatory or review step. It is not claimed to be where Indonesian sellers are; WhatsApp is.

## What this does not do
- **Buyer-side bots (an Instagram DM bot, say) are not built.** They must run per seller, on the seller's own account through the seller's OAuth grant: a buyer talks to
  `@toko-rina`, never to an Etalase account. That is a different product surface (a customer-service bot, a crowded category), needs Meta App Review for messaging permissions
  (not yet verified), and would dilute the agent-commerce story. It is on the roadmap behind the same `Messenger` interface.
- **Two directions, two rules.** Seller ↔ Etalase can use one shared bot or number for all sellers (the seller is Etalase's customer). Buyer ↔ seller must be per tenant.
- LINE, Zalo, Messenger and Instagram as seller channels need their own review and window rules; none is built.
- Per-shop profiles do not describe the shop's business in more detail than the platform's (no business name or logo in the profile: the spec fields for that were not checked), only its own endpoint and scope.
- The official UCP conformance suite was run against the platform host only. The per-shop profile is checked against the SDK's `BusinessSchema` in `tests/test_shop_profiles.py`, not by the suite.
- Pay and order pages (`/pay/<order>`, `/checkout/<session>`) on a shop's address answer "not found" for another shop's order. They are still capability URLs: anyone holding the link sees the page.
- A shop that is not verified shows a holding page on its address and nothing else. Chat sign-up, which creates such shops, is [ADR 0010](0010-chat-signup-address-first.md).

## Consequences
- Adding a channel is one adapter (send, buttons, media download, a webhook parser) plus an address prefix. Telegram took one adapter file, one router, one webhook route and one
  link flow.
- The dashboard sign-in code goes to the chosen channel, so a seller who linked Telegram is not locked out by WhatsApp's 24-hour window.
- Telegram linking uses a code obtained inside a signed-in session. An independent review showed the first version could be replayed from another chat and relinked silently, so the code now works once (an atomic record per code) and a replaced chat is announced to the old chat and the WhatsApp number. Until it is used it is still a bearer value for up to 10 minutes ([doc 09](../09-security-privacy-compliance.md)).
- A Telegram chat can become unreachable for good (the seller blocks the bot). On such a refusal (HTTP 400, 403, 404), not on temporary trouble, a message goes to the seller's WhatsApp number instead, so a seller is never locked out of sign-in or alerts.
- A shop address is a registry entry created atomically, because checking and then saving let two shops win the same name in a race. A released address is held for its previous owner for 30 days, since old links and QR codes still point at it.
- Any host under the base domain except the platform host is a shop or a 404, after the Host header is normalised (trailing dot, port). Before this, `shop.example.com.` fell through to the dashboard.
- Store pages load photos we host from `/media/<file>` on their own origin. The absolute public URL would be a different origin on a shop address and, over plain http (a local try), blocked by the CSP; a real-browser screenshot run found this.
- Subdomains give every shop its own browser origin, so scripts and storage of one shop cannot touch another's. A claimed name can still imitate a brand; the reserved list is
  small and an abuse process is not built.
- Changing a slug breaks links people already shared. The dashboard says so before saving.
- A name made of a bank's, a marketplace's or an "official" word is refused (`slugs.LOOKALIKE_WORDS`, whole words). It is a short list, not a defence against imitation.
