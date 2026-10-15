# ADR 0009: QRIS only in the pilot; cash on delivery is a decision, not an omission

**Status:** accepted for the pilot, to be reopened with the pilot's own numbers. Not legal or financial advice.

## Context

Cash on delivery (COD) is common in Indonesian online shopping. NielsenIQ's 2023 study (five cities) put its share at 56%; one
vendor claims over 90% (neither figure is ours, and they measure different things). A QRIS-only checkout may therefore lose buyers
that a marketplace keeps. We have no data of our own on how many.

The product's promise to a seller is "you are paid for what you pack", and the payout hold and the return window are built on it
(ADR 0003, ADR 0007).

## Decision

1. **The pilot accepts QRIS only.** The checkout, the agent tools and the buy page say so; nothing is left for a buyer or an agent
   to assume.
2. **A request for COD is refused with a reason an agent can repeat to the buyer** ("cash on delivery is not offered: payment is by
   QRIS, before the seller packs the order"). It creates no order and holds no stock. MCP and REST accept a `payment_method`
   argument for exactly this purpose; its only accepted value is `qris`.
3. **We measure what the decision costs instead of guessing.** `GET /tasks/attribution` (operator token) reports, per door and per
   referrer, how many checkouts were started, paid, and expired unpaid, plus the overall `unpaid_rate`. An order that expires unpaid
   is the closest signal we have to a buyer who left at the payment page. It cannot tell us the buyer wanted COD, so the seller
   interviews ask about it directly ([doc 17](../17-market-fit-and-direction.md)).

## Why not COD

- **The seller packs and ships on a promise.** A refused parcel costs the seller both ways of shipping and nothing pays for it.
- **The hold and the refund flow assume money arrived before the parcel left.** With COD there is nothing to hold and nothing to
  refund through us.
- **The courier collects the cash and remits it on its own schedule**, outside anything Etalase can see, hold or verify.
- **It would turn Etalase into a collector of cash in a chain we do not control**, which is a different compliance question from
  the one we are asking counsel (doc 09).

## Consequences

- Some buyers will not complete a QRIS checkout. The pilot's `unpaid_rate` and the interviews decide whether that is acceptable.
- Reopening COD later is a product and legal decision, not a bug fix. It would need: who bears a refused parcel, a deposit or a
  verified-buyer rule to protect the seller, and how the courier's remittance enters the commission.
- Agents are told in the MCP instructions, in every product result (`payment`), and in the tool schema, so none promises COD.

## Alternatives considered

- **Offer COD for trusted sellers and repeat buyers only.** Plausible later; needs a buyer track record we do not have.
- **Silently not mention COD.** Agents would improvise an answer. Rejected.
- **Cash at pickup (the buyer collects from the seller).** A different fulfilment model; not part of this pilot.
