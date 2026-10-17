# 12. Benchmark and test plan

| Metric | How we measure | Target | Status |
|---|---|---|---|
| Overselling under load | 100 concurrent reservations for the last unit (`tests/test_stock.py`); 50 concurrent agent checkouts from different buyers (demo step 8) | 0 units oversold | ✅ Passing on the in-memory store (one lock). The Firestore store uses transactions; the same race against the Firestore emulator is next |
| Duplicate and late payments | Same payment event twice; payment after the hold expired (`tests/test_checkout.py`, `tests/test_security.py`) | No double stock change; late payment refunded | ✅ Automated, passing |
| Payment vs expiry race | Expiry job and payment webhook on the same order (`tests/test_security.py`) | Exactly one wins; paid orders never expire | ✅ Automated, passing |
| Webhook and sign-in abuse | Forged shipping/payment webhooks, missing signatures, OTP brute force, oversized input (`tests/test_security.py`, `tests/test_api.py`) | Rejected | ✅ Automated, passing |
| Catalog extraction accuracy | 43 labelled seller messages (title, variants, price, stock, questions) via `make eval`; photo sets next | ≥ 90% of cases fully correct | Offline-rules baseline **49%** (`prototype/evals/results/latest.md`). The Gemini run needs a key and has not been done |
| Catalog build time | Photos + voice note to published product | < 15 s per product | To run |
| Agent tool latency | p95 of `search_products` and `create_checkout` on Cloud Run | < 2 s | To run |
| Seller alert speed | Payment webhook to WhatsApp message | < 5 s | To run |
| Stock-update understanding | Intent accuracy on 100 real seller messages (Indonesian and English) | ≥ 95% | Offline parser covered by unit tests; Gemini agent to run |

## Running the automated tests

```bash
cd prototype
pip install -r requirements-dev.txt
pytest -q            # 725 tests
make eval            # extraction accuracy: offline rules, plus Gemini when a key is set
make ucp-conformance # Google's official UCP conformance suite against Etalase (see docs/16-ucp.md)
```

## Planned evaluation set

- `prototype/evals/extraction_cases.jsonl` (43 cases, done) and `eval/catalog/`: 50 product photo sets with seller descriptions (Indonesian and English) and the
  expected structured output, scored field by field.
- `eval/seller_messages.jsonl`: 100 real seller messages with expected intents.
- Latency measured from Cloud Run request logs (Cloud Monitoring).

Results go into the deck's benchmark slide before the final submission.
