# UCP conformance: etalase (test mode)

Official suite (github.com/Universal-Commerce-Protocol/conformance), UCP spec 2026-04-08, SDK 0.4.6.

Etalase in its sandbox test mode (UCP_TEST_MODE): the suite's mock card token pays, its mock platform receives signed order webhooks, and PUT /orders and /testing/simulate-shipping exist. Production does not have these.

| Test file | Passed | Failed | Skipped |
|---|---|---|---|
| protocol_test | 2 | 0 | 1 |
| validation_test | 6 | 0 | 0 |
| idempotency_test | 4 | 0 | 0 |
| checkout_lifecycle_test | 11 | 0 | 0 |
| totals_test | 4 | 0 | 1 |
| fulfillment_test | 2 | 0 | 8 |
| fulfillment_structure_test | 2 | 0 | 0 |
| business_logic_test | 4 | 2 | 2 |
| discount_test | 0 | 0 | 6 |
| order_test | 4 | 0 | 0 |
| invalid_input_test | 3 | 0 | 0 |
| binding_test | 1 | 0 | 0 |
| ap2_test | 1 | 0 | 0 |
| card_credential_test | 0 | 1 | 0 |
| webhook_structure_test | 6 | 0 | 0 |
| webhook_test | 1 | 0 | 2 |
| simulation_url_security_test | 3 | 0 | 0 |
| **total** | **54** | **3** | **20** |

Failures and why:

- `BusinessLogicTest.test_discount_flow`: discount capability not implemented (not advertised); the suite does not skip it
- `BusinessLogicTest.test_multiple_discounts_one_rejected`: discount capability not implemented (not advertised); the suite does not skip it
- `CardCredentialTest.test_card_credential_payment`: sends a raw card number (fpan); Etalase never takes card data, so QRIS is the only payment. Test mode takes the mock token only, so it fails there too.
