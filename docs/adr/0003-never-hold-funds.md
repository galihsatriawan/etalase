# ADR 0003: Etalase does not keep seller funds in its own accounts

**Status:** accepted as a design; **legal classification and provider feasibility not yet confirmed** (see "What we do not claim"). The file name keeps the original slug so links still work.

The title used to read "Etalase never holds seller funds". That wording was a legal conclusion we cannot support yet, so it is now a bounded factual statement about the design.

## Context
Holding and forwarding other people's money is a regulated payment activity in Indonesia. We also
want buyers protected until delivery.

## Decision
Payments go to the seller's sub-account at a licensed payment provider. Etalase never receives the buyer's payment in a bank account of its own. Etalase instructs the
provider when to release funds (after delivery and the return window, see
[ADR 0007](0007-payout-hold-and-returns.md)) and how to split the commission. Refunds happen before release.

## What we do not claim

- **Not "we are outside regulation".** Bank Indonesia's regulation PBI 23/6/PBI/2021 lists forwarding funds (disbursement) to goods and services providers among the payment-service activities that need a licence. An instruction to a licensed provider to release or hold a sub-account balance, plus deducting a commission from that flow and controlling the seller's access to the money, may still count as such an activity. We have not had this reviewed.
- **Not "the provider can hold it for us".** We found no Xendit page that documents a hold or scheduled release controlled by the platform (sub-account users withdraw from their own dashboard and, in Indonesia, auto-withdrawal can be changed but not paused or removed). DOKU documents customizable hold periods. Until a provider confirms in writing, the hold is a design behind the `PaymentProvider` interface.
- **Tax.** A platform that uses an escrow account to hold merchants' earnings may fall within the PMK 37/2025 definition of a PPh 22 collector, above a DJP threshold we have not found.
- The open questions for the provider and for counsel are listed in the market-validation review and summarised in [doc 09](../09-security-privacy-compliance.md#payments-regulation).

## Consequences
- `PaymentProvider.release_to_seller` and `refund` are provider calls, not ledger moves in our database.
- KYC and bank details live at the provider; Etalase stores only status and the sub-account id.
- Payout timing depends on the provider's settlement options (Xendit's pages disagree on QRIS settlement, T+1 or T+2 business days; we do not rely on either); release to the seller always waits for confirmed delivery and the buyer's return window.
