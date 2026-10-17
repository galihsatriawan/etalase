# 13. Demo video script (under 3 minutes)

Split screen: **left** a phone with WhatsApp (seller), **right** a laptop with the buyer's AI agent.
Record against the deployed Cloud Run URL with Gemini enabled.

| Time | Scene | What viewers see | Voice-over |
|---|---|---|---|
| 0:00–0:15 | Problem | Rina's WhatsApp full of product photos; an AI assistant that cannot find her | "AI assistants are starting to shop for people. But millions of sellers who sell on WhatsApp are invisible to them." |
| 0:15–0:50 | Catalog from photos | Rina sends 3 photos and a voice note; Etalase replies with products, variants, price, weight, readiness score; she taps **Publish** | "Rina just sends what she already has. Gemini turns photos and a voice note into a structured catalog." |
| 0:50–1:30 | Agent buys | Buyer asks "black voal hijab under 100k to Bandung"; the agent calls Etalase over MCP, shows the seller's return policy and courier price, buyer says yes, QRIS appears | "Any agent can find her products, see how she handles returns, and check out, with stock held while the buyer pays." |
| 1:30–1:50 | Order in WhatsApp | Payment confirmed; Rina's phone buzzes "New paid order" with **Accept**; she taps it and later replies "tracking JNE123456" | "Rina never leaves WhatsApp." |
| 1:50–2:10 | Trust: stock | Low stock: Rina gets "Is it still available?" before the buyer pays; 50 agents race for the last unit, exactly one wins | "Etalase never sells what she doesn't have." |
| 2:10–2:40 | Trust: returns | Delivery webhook → Rina sees "payout on <date> if the buyer does not open a return". The buyer's agent calls `request_return` (damaged, with a photo link); Rina taps **Approve return**, later **Item received**; the buyer is refunded | "The money waits until the return window is over, so a refund never has to be taken back from Rina. If nothing is wrong, she is paid, minus a small fee." |
| 2:40–2:55 | Close | Architecture slide (Cloud Run, Gemini, ADK, MCP, Connector API), logo and URL | "Etalase. Every seller, shoppable by AI." |

## Optional cutaways (only if the cut stays under 3:00)

- **The buyer without an agent (3–4 s, during 0:50–1:30):** a phone opening the shop link: product grid, a product page, the QRIS code. It shows that the same stock and rules sit behind a page a person can use. Record it from `scripts/demo.py` data or the deployed store link.
- **A second channel (3–4 s, during 1:30–1:50):** the same "New paid order" alert with the Accept button arriving in Telegram, from a seller who linked it in the dashboard. Say: "Same order, any channel." Needs `TELEGRAM_BOT_TOKEN` and the webhook set (SETUP.md section 3b); offline, `scripts/e2e_http.py` shows it.
- Do not claim an Instagram bot: it is roadmap only ([doc 17](17-market-fit-and-direction.md)).

## Preparation checklist

- [ ] Cloud Run deployed with `GOOGLE_GENAI_USE_VERTEXAI=true` and a working Gemini model
- [ ] The reviewers' sandbox deployed as its own service (`DEMO=1 PROJECT_ID=... make deploy`) and its `/demo` link put in the submission form; `make e2e-judge` passes against it (`python scripts/e2e_judge.py <url>`)
- [ ] WhatsApp Cloud API test number connected, Rina's phone registered as a test recipient
- [ ] Buyer agent running (`python -m etalase.agents.buyer_agent`) against the deployed `/mcp/`
- [ ] Payment in test mode; delivery triggered through `/dev/deliver/{order_id}`; the return window skipped with `/dev/release/{order_id}` (it moves the due date and runs the timers). To show a return instead, call `request_return` before releasing
- [ ] The return buttons on a real phone need the `return_requested` and `return_approved` templates approved, or the 24-hour window open (message the Etalase number first). `scripts/demo.py` step 6b shows the same flow offline
- [ ] Captions in English; upload to YouTube (unlisted is not enough: it must be public) or a public Drive link
