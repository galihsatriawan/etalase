# ADR 0002: One stock owner per seller

**Status:** accepted

## Context
Sellers sell in several places. If two systems can both change stock without rules, the same unit is
sold twice.

## Decision
Each seller has exactly one stock owner: Etalase (`internal`), their OMS (`oms`) or their store
(`store`). Only the owner changes `on_hand`. When the owner is external, chat stock updates are
refused and agent orders are sent to the owner.

## Consequences
- `Seller.stock_owner` decides who may write stock and the sell mode (external owners → instant).
- A seller with both an OMS and a website connects through the OMS.
- Google Sheets is an input to Etalase's stock, not an owner.
