# ADR 0005: Adapters and country packs

**Status:** accepted

## Context
Payment methods, couriers, messaging and tax rules differ by country. Stripe does not support QRIS
in Indonesia; Indonesian couriers are best reached through an aggregator.

## Decision
Every external dependency sits behind a small interface (`PaymentProvider`, `ShippingProvider`,
`Messenger`, `Store`). A `CountryPack` names the currency, payment provider, shipping provider and tax
profile per market. Mock implementations exist for each interface.

## Consequences
- Indonesia: Xendit + Biteship + WhatsApp Cloud API. Other markets add a config entry and any
  missing adapter.
- The whole flow runs locally with no credentials, which keeps tests fast and the demo reliable.
