# ADR 0006: A Connector API with an OpenAPI spec

**Status:** accepted

## Context
There are many OMSs, e-commerce platforms and local website builders. Writing and maintaining a pull
adapter for each one does not scale.

## Decision
Offer a push-style Connector API (`/connector/v1`) documented by an OpenAPI spec: partners upsert
products, update stock and report order status; Etalase sends paid orders to their webhook, signed
with HMAC-SHA256. Keys are per connection and stored hashed. Pull adapters remain for large
platforms with open APIs (WooCommerce, Jubelio, Ginee, Google Sheets).

## Consequences
- One integration by a builder enables all its merchants for AI agents, which also creates a
  partnership channel.
- The connected system becomes the seller's stock owner (ADR 0002).
- Webhook delivery needs retries and a dead-letter queue (Pub/Sub) before the pilot.
