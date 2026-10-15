# 13. Demo video script (under 3 minutes)

Split screen: **left** a phone with WhatsApp (seller), **right** a laptop with the buyer's AI agent.
Record against the deployed Cloud Run URL with Gemini enabled.

| Time | Scene | What viewers see | Voice-over |
|---|---|---|---|
| 0:00–0:20 | Problem | Rina's WhatsApp full of product photos; an AI assistant that cannot find her | "AI assistants are starting to shop for people. But millions of sellers who sell on WhatsApp are invisible to them." |
| 0:20–0:55 | Catalog from photos | Rina sends 3 photos and a voice note; Etalase replies with products, variants, price, weight, readiness score; she taps **Publish** | "Rina just sends what she already has. Gemini turns photos and a voice note into a structured catalog." |
| 0:55–1:40 | Agent buys | Buyer asks "black voal hijab under 100k to Bandung"; the agent calls Etalase over MCP, shows courier price, buyer says yes, QRIS appears | "Any agent can now find her products and check out, with stock held while the buyer pays." |
| 1:40–2:05 | Order in WhatsApp | Payment confirmed; Rina's phone buzzes "New paid order" with **Accept**; she taps it and later replies "tracking JNE123456" | "Rina never leaves WhatsApp." |
| 2:05–2:25 | Trust | Low stock: Rina gets "Is it still available?" before the buyer pays; 50 agents race for the last unit, exactly one wins | "Etalase never sells what she doesn't have." |
| 2:25–2:45 | Payout and scale | Delivery webhook → payout message; dashboard; architecture slide (Cloud Run, Gemini, ADK, MCP, Connector API) | "She's paid after delivery, minus a small fee. And any OMS or website builder can plug in with one API." |
| 2:45–2:55 | Close | Logo and URL | "Etalase. Every seller, shoppable by AI." |

## Preparation checklist

- [ ] Cloud Run deployed with `GOOGLE_GENAI_USE_VERTEXAI=true` and a working Gemini model
- [ ] WhatsApp Cloud API test number connected, Rina's phone registered as a test recipient
- [ ] Buyer agent running (`python -m etalase.agents.buyer_agent`) against the deployed `/mcp/`
- [ ] Payment in test mode; delivery triggered through `/dev/deliver/{order_id}`
- [ ] Captions in English; upload to YouTube (unlisted is not enough: it must be public) or a public Drive link
