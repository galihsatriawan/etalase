# 12. Benchmark and test plan

| Metric | How we measure | Target | Status |
|---|---|---|---|
| Overselling under load | 100 concurrent reservations for the last unit (`tests/test_stock.py`); 50 concurrent agent checkouts from different buyers (demo step 8) | 0 units oversold | ✅ Passing on the in-memory store (one lock). The Firestore store uses transactions; the same race against the Firestore emulator is next |
| Duplicate and late payments | Same payment event twice; payment after the hold expired (`tests/test_checkout.py`, `tests/test_security.py`) | No double stock change; late payment refunded | ✅ Automated, passing |
| Payment vs expiry race | Expiry job and payment webhook on the same order (`tests/test_security.py`) | Exactly one wins; paid orders never expire | ✅ Automated, passing |
| Webhook and sign-in abuse | Forged shipping/payment webhooks, missing signatures, OTP brute force, oversized input (`tests/test_security.py`, `tests/test_api.py`) | Rejected | ✅ Automated, passing |
| Catalog extraction accuracy | 43 labelled seller messages (title, variants, price, stock, questions) via `make eval`; photo sets next | ≥ 90% of cases fully correct | Gemini 3.8 Flash **84%** (36/43), offline-rules baseline 49%: **below the target**. Text only, one run; photos and voice are not in the set. Details below (`prototype/evals/results/latest.md`) |
| Catalog build time | Photos + voice note to published product | < 15 s per product | Partly: median 5.6 s per text message with Gemini (43 messages, one run, retries included). Photos and voice are not timed |
| Agent tool latency | p95 of `search_products` and `create_checkout` on Cloud Run | < 2 s | To run |
| Seller alert speed | Payment webhook to WhatsApp message | < 5 s | To run |
| Stock-update understanding | Intent accuracy on 100 real seller messages (Indonesian and English) | ≥ 95% | Offline parser covered by unit tests; Gemini agent to run |

## Catalog extraction: first Gemini run (text only)

Run on 17 Oct 2026 (UTC) with `python -m evals.run_eval --extractor gemini --runs 1 --delay 1 --verbose`: `gemini-3.8-flash`,
43 labelled seller messages, one run per case. The full report is `prototype/evals/results/latest.md`. Photos and voice notes
are not part of this set, and one run per case says nothing about run-to-run variation.

| | Offline rules | Gemini 3.8 Flash |
|---|---|---|
| Cases fully correct (target ≥ 90%) | 21/43 (49%) | **36/43 (84%)** |
| Title | 27/42 (64%) | 42/42 (100%) |
| Price | 33/42 (79%) | 42/42 (100%) |
| Variants (colour / size set) | 24/42 (57%) | 37/42 (88%) |
| Stock per variant | 24/42 (57%) | 37/42 (88%) |
| Weight, when the seller stated it | 2/3 | 2/3 |
| Asks when information is missing | 1/6 (17%) | 6/6 (100%) |
| Invented prices | 0 | 0 |
| Invented products / missed products | 6 / 8 | 2 / 0 |
| Median time per message | 0.00 s | 5.60 s |

Gemini is clearly better than the rules and still short of the 90% target. The seven failures fall into two groups:

- **A measurement is put in the size field** (`c24`, `c25`, `c30`, `c31`, `c32`): "350ml", "110x110", "200g", "250gr" and "30ml" became a variant size.
  The stock quantities are right; a shop would show a size picker with one odd option. In `c30` the model also returned a weight of 250 g for a
  stated 200 g, which would change the shipping quote.
- **Chatter became a product** (`c42`, `c43`): the buyer's question "Kak ada hijab warna merah?" became "Hijab Merah", and the stock update "Hitam sold out"
  became a product called "Produk".

Two numbers in the report need reading with care:

- **Invented stock numbers: 6.** All six come from the five unit cases above. The quantity is correct, but it sits on a variant with an extra size label,
  and the scorer counts that as a stock number the seller never gave (`evals/scoring.py`). No quantity was made up. The scorer was not changed after the run.
- **Questions when nothing was missing: 27 of 37.** The prompt tells the model to ask whenever price, stock or weight is missing (`catalog/extractor.py`),
  and most messages do not state a weight, so many of these are probably weight questions. This is not confirmed: the run stored whether the model
  asked, not what it asked.

Not fixed yet. Both groups look like prompt and schema problems (say what the size field is for; say that a message without a product yields no product),
and any change needs a new run before a new number is quoted.

## Running the automated tests

```bash
cd prototype
pip install -r requirements-dev.txt
pytest -q            # 735 tests
make eval            # extraction accuracy: offline rules, plus Gemini when a key is set
make ucp-conformance # Google's official UCP conformance suite against Etalase (see docs/16-ucp.md)
```

## Planned evaluation set

- `prototype/evals/extraction_cases.jsonl` (43 cases, done) and `eval/catalog/`: 50 product photo sets with seller descriptions (Indonesian and English) and the
  expected structured output, scored field by field.
- `eval/seller_messages.jsonl`: 100 real seller messages with expected intents.
- Latency measured from Cloud Run request logs (Cloud Monitoring).

Results go into the deck's benchmark slide before the final submission.
