# ADR 0003: Etalase never holds seller funds

**Status:** accepted (to verify with the payment provider and legal counsel)

## Context
Holding and forwarding other people's money is a regulated payment activity in Indonesia. We also
want buyers protected until delivery.

## Decision
Payments go to the seller's sub-account at a licensed payment provider. Etalase instructs the
provider when to release funds (after delivery and the return window, see
[ADR 0007](0007-payout-hold-and-returns.md)) and how to split the commission. Refunds happen before release.

## Consequences
- `PaymentProvider.release_to_seller` and `refund` are provider calls, not ledger moves in our database.
- KYC and bank details live at the provider; Etalase stores only status and the sub-account id.
- Payout timing depends on the provider's settlement options (for example, Xendit settles QRIS at T+2
  business days); release to the seller always waits for confirmed delivery and the buyer's return window.
