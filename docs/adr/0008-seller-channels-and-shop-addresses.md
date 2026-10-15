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

## Why Telegram first
A bot can write to a user at any time once the user has started it: no 24-hour window, no templates, no per-message fee. It is the cheapest honest demonstration that one order can
reach a seller on another channel, and the seller side has no regulatory or review step. It is not claimed to be where Indonesian sellers are; WhatsApp is.

## What this does not do
- **Buyer-side bots (an Instagram DM bot, say) are not built.** They must run per seller, on the seller's own account through the seller's OAuth grant: a buyer talks to
  `@toko-rina`, never to an Etalase account. That is a different product surface (a customer-service bot, a crowded category), needs Meta App Review for messaging permissions
  (not yet verified), and would dilute the agent-commerce story. It is on the roadmap behind the same `Messenger` interface.
- **Two directions, two rules.** Seller ↔ Etalase can use one shared bot or number for all sellers (the seller is Etalase's customer). Buyer ↔ seller must be per tenant.
- LINE, Zalo, Messenger and Instagram as seller channels need their own review and window rules; none is built.
- Per-shop UCP profiles on the subdomain are not built; the platform profile at `/.well-known/ucp` is served on every host.

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
