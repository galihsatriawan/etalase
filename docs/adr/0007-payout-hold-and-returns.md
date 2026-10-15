# ADR 0007: Hold the payout through a return window, and make returns a first-class flow

**Status:** accepted for the prototype. What the payment provider can do (holding a balance, refunds), and the legal reading,
still need to be confirmed (see "Open items"). Not legal advice.

## Context

A buyer's AI agent cannot inspect the goods and the seller is a stranger. Two questions come up in every conversation about
Etalase: "what if the seller is not legitimate?" and "what about returns?". Facts we found on 2026-10-15:

- **Refunds cannot be taken back from a seller.** Once money reached a seller's bank account, a refund needs the seller's cooperation.
  The only reliable design is to refund before the payout, so the payout must wait.
- **Xendit's QRIS refund rules are narrow and its pages disagree.** One help article says a QR payment can be refunded within 7 days,
  another page says 30. The payment fee and VAT come back only on a full refund within 24 hours. Partial refunds are described both ways.
  If the payer's issuer is not supported, a disbursement is needed instead. Refunds go through `POST /refunds` (V2), which answers `PENDING`
  and confirms with a `refund.succeeded` webhook.
- **Indonesian rules, as far as we read them (Permendag 31/2023, through a summary, not the text):** an e-commerce operator must run a
  complaint service (Pasal 9–10), and a social-commerce operator may not facilitate payments (Pasal 21(3)). We found no rule that
  prescribes returns or escrow. Whether Etalase needs a PMSE business licence, and how it is classified, is for a lawyer.
- **Seller identity is not checked by us.** `kyc_status` is a field set by a dev endpoint; real verification is meant to happen at the payment
  provider (ADR 0003) and is not built.
- **Competitors we looked at** (their own pages): cshub lets an AI create orders and payment links inside WhatsApp; Jubelio is an order and
  inventory system. Neither page mentions a payout hold or return flow, which is not proof they have none.

## Decision

1. **Hold the payout for a return window after courier-confirmed delivery.** `PAYOUT_HOLD_HOURS` = 72 by default; 168 hours for a seller with
   fewer than 5 completed orders (an unproven seller is where refunds are most likely). `0` turns the hold and returns off.
2. **Returns are a flow with owners and timers**, not a support ticket: `request_return` (buyer's agent; needs the checkout id *and* the buyer's
   phone) → the seller approves or disputes on WhatsApp (silence for 48 hours = approval) → the item comes back and the seller confirms → refund.
   An approved return that never arrives lapses after 7 days and the seller is paid; a buyer who reported a tracking number gets an operator
   instead. A dispute freezes the money and goes to an operator.
3. **Reasons and refunds are fixed by the platform, only one thing is the seller's choice.** Damaged, wrong, not-as-described items are always
   returnable in the window (full refund, shipping included). A change of mind only if the seller opts in (product price only). **In both cases the
   buyer pays the return shipping and Etalase does not reimburse it in this version**; this is an open decision (see Consequences).
4. **A parcel the courier returns to the seller is refunded automatically** and the stock restored. Before this change such an order stayed
   `shipped` forever with the buyer's money neither refunded nor paid out. *(Updated 2026-10-16.)* The same holds for a parcel the courier
   reports as lost or disposed of (Biteship `disposed`): the buyer is refunded in full, nothing is restocked, the payout never happens. A parcel
   on its way back (`return_in_transit`) moves nothing until it arrives. Each of these is confirmed with the courier's own tracking record before
   money moves, and a recheck every `SHIPMENT_RECHECK_HOURS` catches a webhook that was lost or came too early.
5. **The policy and a small track record are shown to agents** before they buy: in MCP product results, a UCP `refund_policy` link,
   schema.org `MerchantReturnPolicy`, and a public page. An agent can then weigh the seller; this is the honest substitute for KYC we do not have.

## Consequences

- Sellers wait longer for money (delivery + 3 or 7 days + provider settlement). This is the price of buyer protection and is similar in spirit to
  marketplace holds; it must be tested in seller interviews.
- Commission is taken only when the payout is released, so a refunded order earns nothing.
- Operators have a queue (`GET /tasks/disputes`) and two decisions. Volume is unknown; the prototype exposes the work, it does not automate it.
- Abuse is possible in both directions (fake damage claims; a seller who never confirms receipt). The controls are: evidence links (stored, never
  fetched), the seller's right to dispute, silence-means-approval with refund only after the item is back, the tracking-number escalation, and
  per-IP limits (10 return calls per hour) on the agent tools. There is no per-buyer return limit yet.
- Until Xendit refunds are automated, every refund is `refund_pending` and an operator sends it.
- The buyer pays the return shipping even for a defective item. This is the simplest rule to state and build, and the harshest for a buyer; it is shown to
  agents and on the return page so nobody is surprised, and it is the first thing to revisit after interviews.

## Alternatives considered

- **Pay out at delivery and absorb refunds.** The platform would carry every loss, and sellers could not be pursued. Rejected.
- **Etalase holds the money in its own account.** Breaks ADR 0003 and may be a regulated payment activity.
- **Wait for the buyer to confirm receipt.** Buyers act through agents and have no screen of ours; an agent cannot be relied on to confirm.
- **No change-of-mind returns at all.** Simpler, but marketplaces offer them and some sellers want to. Kept as the seller's opt-in.

## Open items

1. Can xenPlatform keep a sub-account balance unreleased for the hold, and what does the split on release look like?
2. Xendit: refund validity, partial refunds, and who bears the payment fee on a refunded order.
3. Who bears the courier cost: reimbursing a defect return's shipping (from the held payout, a prepaid label or an allowance), a parcel that comes back by itself, and a parcel the courier loses (today: buyer refunded in full, the seller carries the loss and the insurance claim, the cost is otherwise unassigned).
4. Legal review: PMSE licence, complaint-service duties, consumer-protection duties on returns, and whether the hold changes how Etalase is classified.
5. Real seller KYC and a per-buyer return limit before real money moves.
