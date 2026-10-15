# ADR 0004: One seller agent plus deterministic handlers

**Status:** accepted

## Context
Multi-agent designs add latency, cost and debugging effort. WhatsApp users expect quick replies, and
money or stock decisions must be exact.

## Decision
- One ADK `LlmAgent` for free-text seller messages, with tools that call the same services as the
  rest of the system. The seller id is taken from session state, never from the model.
- Buttons, payment webhooks, courier webhooks and reservations are handled in code, never by the model.
- Catalog extraction is a single Gemini call with a JSON schema, followed by deterministic
  normalisation and scoring.
- A separate ADK buyer agent exists only for the demo, talking to our public MCP server.

## Consequences
- Fast, cheap, testable. Without Gemini the same actions run through an offline intent parser.
- More agents (payout reconciliation, analytics) can be added when their tools and instructions
  clearly differ.
