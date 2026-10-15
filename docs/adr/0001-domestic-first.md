# ADR 0001: Domestic delivery first

**Status:** accepted

## Context
Cross-border orders add customs duties, HS codes, restricted goods, currency conversion and
expensive returns. Each of these is a product of its own.

## Decision
An order is allowed only when the delivery country equals the seller's country (and is a market with
a country pack). Buyers can be anywhere; the delivery address cannot.

## Consequences
- Checkout validates `buyer.country in product.ships_to` and in `COUNTRY_PACKS`.
- Products carry `ships_to`, so agents are not shown items they cannot deliver.
- Cross-border is roadmap phase 4, reusing the same adapters.
