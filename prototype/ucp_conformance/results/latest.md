# UCP conformance: etalase (production mode)

Official suite (github.com/Universal-Commerce-Protocol/conformance), UCP spec 2026-04-08, SDK 0.4.6.

Etalase as it runs in production: QRIS is the only way to pay, so the tests that pay with a card cannot get an order.

| Test file | Passed | Failed | Skipped |
|---|---|---|---|
| protocol_test | 2 | 0 | 1 |
| validation_test | 5 | 1 | 0 |
| idempotency_test | 4 | 0 | 0 |
| checkout_lifecycle_test | 7 | 4 | 0 |
| totals_test | 4 | 0 | 1 |
| fulfillment_test | 2 | 0 | 8 |
| fulfillment_structure_test | 2 | 0 | 0 |
| business_logic_test | 4 | 2 | 2 |
| discount_test | 0 | 0 | 6 |
| order_test | 0 | 4 | 0 |
| invalid_input_test | 1 | 2 | 0 |
| binding_test | 0 | 1 | 0 |
| ap2_test | 0 | 1 | 0 |
| card_credential_test | 0 | 1 | 0 |
| webhook_structure_test | 0 | 6 | 0 |
| webhook_test | 0 | 1 | 2 |
| simulation_url_security_test | 0 | 3 | 0 |
| **total** | **31** | **26** | **20** |

Failures and why:

- `ValidationTest.test_payment_failure`: needs a declinable card instrument (the suite's fail_token); production has no payment handlers. Passes in test mode.
- `CheckoutLifecycleTest.test_cannot_cancel_completed_checkout`: needs a completed checkout, which production reaches only after the buyer pays the QRIS code. Passes in test mode.
- `CheckoutLifecycleTest.test_cannot_update_completed_checkout`: needs a completed checkout, which production reaches only after the buyer pays the QRIS code. Passes in test mode.
- `CheckoutLifecycleTest.test_complete_checkout`: asserts status completed straight after complete with a card instrument; production answers requires_escalation with a continue_url and the checkout becomes completed after the buyer pays. Passes in test mode.
- `CheckoutLifecycleTest.test_complete_is_idempotent`: needs a completed checkout, which production reaches only after the buyer pays the QRIS code. Passes in test mode.
- `BusinessLogicTest.test_discount_flow`: discount capability not implemented (not advertised); the suite does not skip it
- `BusinessLogicTest.test_multiple_discounts_one_rejected`: discount capability not implemented (not advertised); the suite does not skip it
- `OrderTest.test_order_adjustments`: needs an order paid with the suite's mock card; production takes QRIS only. Passes in test mode.
- `OrderTest.test_order_fulfillment_retrieval`: needs an order paid with the suite's mock card; production takes QRIS only. Passes in test mode.
- `OrderTest.test_order_retrieval`: needs an order paid with the suite's mock card; production takes QRIS only. Passes in test mode.
- `OrderTest.test_order_update`: needs an order paid with the suite's mock card; production takes QRIS only. Passes in test mode.
- `InvalidInputTest.test_invalid_adjustment_status`: needs an order paid with the suite's mock card and PUT /orders, which production does not offer. Passes in test mode.
- `InvalidInputTest.test_malformed_adjustment_payload`: needs an order paid with the suite's mock card and PUT /orders, which production does not offer. Passes in test mode.
- `TokenBindingTest.test_token_binding_completion`: needs a checkout that completes straight after complete (the suite pays with its mock card); production takes QRIS only. Passes in test mode.
- `Ap2MandateTest.test_ap2_mandate_completion`: needs a checkout that completes straight after complete (the suite pays with its mock card); production takes QRIS only. Passes in test mode.
- `CardCredentialTest.test_card_credential_payment`: sends a raw card number (fpan); Etalase never takes card data, so QRIS is the only payment. Test mode takes the mock token only, so it fails there too.
- `WebhookStructureTest.test_delivery_carries_standard_webhook_headers`: needs an order paid with the suite's mock card so that its mock platform receives the webhook; production takes QRIS only. Passes in test mode.
- `WebhookStructureTest.test_order_created_event_is_full_order_entity`: needs an order paid with the suite's mock card so that its mock platform receives the webhook; production takes QRIS only. Passes in test mode.
- `WebhookStructureTest.test_signature_covers_ucp_agent`: needs an order paid with the suite's mock card so that its mock platform receives the webhook; production takes QRIS only. Passes in test mode.
- `WebhookStructureTest.test_signed_delivery_carries_required_headers_and_digest`: needs an order paid with the suite's mock card so that its mock platform receives the webhook; production takes QRIS only. Passes in test mode.
- `WebhookStructureTest.test_transient_failure_retries_preserve_event_identity`: needs an order paid with the suite's mock card so that its mock platform receives the webhook; production takes QRIS only. Passes in test mode.
- `WebhookStructureTest.test_update_event_is_full_order_entity`: needs an order paid with the suite's mock card so that its mock platform receives the webhook; production takes QRIS only. Passes in test mode.
- `WebhookTest.test_webhook_event_stream`: needs an order paid with the suite's mock card and /testing/simulate-shipping, neither of which production has. Passes in test mode.
- `SecurityTest.test_simulation_endpoint_correct_secret`: tests the sample server's /testing/simulate-* hook, which a production business does not have. Passes in test mode.
- `SecurityTest.test_simulation_endpoint_incorrect_secret`: tests the sample server's /testing/simulate-* hook, which a production business does not have. Passes in test mode.
- `SecurityTest.test_simulation_endpoint_missing_header`: tests the sample server's /testing/simulate-* hook, which a production business does not have. Passes in test mode.
