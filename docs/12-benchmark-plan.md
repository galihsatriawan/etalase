# 12. Benchmark and test plan

| Metric | How we measure | Target | Status |
|---|---|---|---|
| Overselling under load | 100 concurrent reservations for the last unit (`tests/test_stock.py`); 50 concurrent agent checkouts from different buyers (demo step 8) | 0 units oversold | ✅ Passing on the in-memory store (one lock). The Firestore store uses transactions; the same race against the Firestore emulator is next |
| Duplicate and late payments | Same payment event twice; payment after the hold expired (`tests/test_checkout.py`, `tests/test_security.py`) | No double stock change; late payment refunded | ✅ Automated, passing |
| Payment vs expiry race | Expiry job and payment webhook on the same order (`tests/test_security.py`) | Exactly one wins; paid orders never expire | ✅ Automated, passing |
| Webhook and sign-in abuse | Forged shipping/payment webhooks, missing signatures, OTP brute force, oversized input (`tests/test_security.py`, `tests/test_api.py`) | Rejected | ✅ Automated, passing |
| Catalog extraction accuracy | 43 labelled seller messages (title, variants, price, stock, questions) via `make eval`; photo sets next | ≥ 90% of cases fully correct | Gemini 3.8 Flash **98%** (42/43) after a prompt fix made on the same messages; **84%** (36/43) before it, offline rules 49%. So not a held-out result. Text only, one run; photos and voice are not in the set. Details below (`prototype/evals/results/latest.md`) |
| Catalog build time | Photos + voice note to published product | < 15 s per product | Partly: median 4.8 s per text message with Gemini 3.8 Flash (43 messages, one run, retries included). Photos and voice are not timed |
| Agent tool latency | p95 of `search_products` and `create_checkout` on Cloud Run | < 2 s | To run |
| Seller alert speed | Payment webhook to WhatsApp message | < 5 s | To run |
| Stock-update understanding | Intent accuracy on 100 real seller messages (Indonesian and English) | ≥ 95% | Offline parser covered by unit tests; Gemini agent to run |

## Catalog extraction: Gemini runs (text only)

Three Gemini runs and the offline baseline on the same 43 labelled seller messages, one run per case, all on 17 Oct 2026 (UTC)
(`python -m evals.run_eval --extractor gemini --runs 1 --delay 1 --verbose`). Reports: `prototype/evals/results/latest.md` (current prompt) and
`first-prompt-gemini-3.8-flash.md` (the first run). Photos and voice notes are not in this set, and one run per case says nothing about
run-to-run variation.

| | Offline rules | 3.8 Flash, first prompt | 3.8 Flash, current prompt | 3.1 Flash-Lite, current prompt |
|---|---|---|---|---|
| Cases fully correct (target ≥ 90%) | 21/43 (49%) | 36/43 (84%) | **42/43 (98%)** | 36/43 (84%) |
| Title | 27/42 | 42/42 | 41/42 | 41/42 |
| Price | 33/42 | 42/42 | 42/42 | 41/42 |
| Variants (colour / size set) | 24/42 | 37/42 | 42/42 | 42/42 |
| Stock per variant | 24/42 | 37/42 | 42/42 | 39/42 |
| Weight, when the seller stated it | 2/3 | 2/3 | 3/3 | 2/3 |
| Asks when information is missing | 1/6 | 6/6 | 6/6 | 5/6 |
| Questions when nothing was missing (lower is better) | 0 of 37 | 27 of 37 | 3 of 37 | 7 of 37 |
| Invented prices / invented stock numbers | 0 / 1 | 0 / 6 | 0 / 0 | 0 / 0 |
| Invented products / missed products | 6 / 8 | 2 / 0 | 0 / 0 | 0 / 0 |
| Median time per message | 0.00 s | 5.60 s | 4.78 s | 1.22 s |

**Read the 98% with care.** The first run (84%) was a clean test. We then changed the prompt using its seven misses and ran the same 43
messages again, so the 98% is a score on the messages we tuned on, not an estimate for new ones. The changes are general rules, not the
messages themselves: a measure that describes one product (350ml, 200g, 110x110) is not a size; a message that is not a listing yields no
products; a stated weight is used as given; weight is never asked for. A fresh set of messages is the way to find out how well they carry over.

What the change fixed: the five cases where a measurement had landed in the size field (and with them the six "invented stock numbers", which were
the right quantities attached to a variant with an extra size), the two cases where chat became a product, and the stated weight in `c30`.
Questions asked when nothing was missing fell from 27 to 3, which fits those having been mostly weight questions. The one miss left is `c25`:
the title "Hijab Voal Premium Lasercut 110x110" has three words more than the expected "hijab voal", and the scorer allows two.

**Flash-Lite is faster and cheaper, and not good enough here.** Gemini 3.1 Flash-Lite lists at $0.25 / $1.50 per million tokens (input / output)
against $0.75 / $3.75 for 3.8 Flash until the end of 2026 (Vertex AI pricing page, 18 Oct 2026), and answered in 1.22 s instead of 4.78 s. With the same prompt
it got 36/43. It returned 600000 for "1,2jt" with two pieces (`c11`), dropped stock numbers in three cases (`c10`, `c12`, `c36`), did not ask
about a missing stock (`c37`) and returned 250 g for a stated 200 g (`c30`). A wrong price or stock costs a seller more than the tokens, so
extraction stays on 3.8 Flash.

Still to do: a held-out set of new messages, photos and voice notes, and repeated runs to see how much a score moves from one run to the next.

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
