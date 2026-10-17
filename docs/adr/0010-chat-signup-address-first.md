# ADR 0010: Sign-up in chat, the address chosen first, and a shop nobody can see until it is verified

**Status:** accepted (2026-10-17). Built and tested with simulated messages; **off by default** (`SIGNUP_ENABLED`) and never run against live WhatsApp traffic. The identity and bank
check it waits for is **not built**.

## Context
The seller Etalase is for is one who sells in chat and has no store of their own (some also list on a marketplace; what they lack is a store page and a checkout that a buyer or an agent can use) ([doc 17](../17-market-fit-and-direction.md)). What we give such a seller is an address of their
own (`rina.<domain>`), products that agents can find and buy, and a catalog built from the photos and sentences they already send. The prototype could not give anyone that
without us: sellers were created with a dev endpoint.

Two things make an open door risky. The address is the first thing a buyer reads, so a name like `bca-resmi` lends a scam shop a bank's authority ([doc 09](../09-security-privacy-compliance.md#impersonation-and-phishing)). And a seller who has not been
checked must not take a buyer's money or appear to an agent as a business.

## Decision
1. **The address is chosen at the start, in the chat.** "hi" → the shop name → an address proposed from it (`rina-hijab`, or `rina-hijab-2` if taken) with a **Use it** button, or the
   seller types another → where they ship from (street, city, postal code in one message). Uniqueness is the atomic registry entry of ADR 0008, so two shops tapping the same name
   at once get one winner and the other is offered the next free name. The first message also says once what Etalase never asks for (a code, PIN or password, or a transfer).
2. **A new shop is `pending` and invisible.** `Seller.open_for_buyers` is true only when `kyc_status` is `verified`. For a pending shop the store page, its product pages, its schema.org data, its
   return pages, search, `get_product`, shipping quotes, UCP and the MCP/REST checkout all answer "not found", and its own address shows a holding page that names nothing. The seller can still
   send photos and descriptions, and sees the catalog take shape in the chat; **Publish** says the products are saved and the shop is not open yet. The order service already refused an
   unverified seller; now nothing about the shop is shown either.
3. **Verification.** With the mock payment provider (a local run, the sandbox) a finished sign-up is verified at once, since there is no identity to check. With a real one, **an operator verifies by
   hand** (`GET /tasks/pending-sellers`, `POST /tasks/verify-seller`, header `X-Etalase-Operator-Token`) after checking identity and bank account outside Etalase. This is the pilot's stand-in for
   the payment provider's own check through a link, which is the design in [doc 02](../02-product.md) and is **not built**. Turning sign-up on in prod without an `OPERATOR_TOKEN` is refused at start-up.
4. **A hold, not a claim.** An address is held for `SIGNUP_HOLD_HOURS` (72) from sign-up. If the shop is not verified by then the background job gives the address back **at once** (unlike a seller who moves,
   whose old address stays reserved for 30 days: an unverified shop has no links out there). The seller is told, and when they next write they choose an address again. A verified shop keeps its address.
5. **Limits.** One shop per number (the number is proven by Meta's signed webhook, and a first-message race makes one shop, not two); `SIGNUP_SHOPS_PER_HOUR` (20) across the platform, after which
   strangers are asked to come back in an hour; a stranger's photo or voice note is answered, not downloaded; and names and addresses may not hold a link, an @handle or a phone number, or
   a word from a short list that reads as a bank, a marketplace, an official account or Etalase (`slugs.LOOKALIKE_WORDS`: whole words only, `bca` is refused, `bcaffee` is not).
6. **Telegram never starts a shop.** WhatsApp is the identity; a Telegram chat is linked later from the dashboard.

## What this does not do
- **No payment-provider verification link.** An operator's decision replaces it; that does not scale past a pilot and is a person's judgment, not a check.
- **The word list is short and needs an owner.** It stops the obvious `bca-resmi`; it does not stop `bca-resm1`, a misspelling, or a name that imitates a seller's brand. There is no report button
  or abuse process ([doc 09](../09-security-privacy-compliance.md)).
- **No preview of a pending shop** to the seller beyond the chat.
- **The "your shop is open" message may not arrive.** Outside WhatsApp's 24-hour window it needs an approved template, and none is defined for it. The same goes for the "address given back" notice.
- Error reasons in the chat (a bad name, a refused address) are in English whatever `SELLER_LOCALE` is.
- Not run against Firestore: the new `pending_sellers` query is untested there, as is the rest of that store ([known gaps](../../learn/master/known-gaps.md)).

## Alternatives considered
- **Claim the address at the end, after the catalog.** The seller invests two minutes and then loses the name they said aloud to a friend. Choosing first also makes uniqueness part of the conversation instead of a failure at the end.
- **A web form for sign-up.** Possible later, and the registry is the same. In chat the number is already proven and the seller is already where they sell.
- **Verify automatically after a delay or on the first order.** That would put an unchecked shop in front of buyers and agents, which is the thing the pending state exists to prevent.
