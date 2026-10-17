# UCP conformance: etalase

Official suite (github.com/Universal-Commerce-Protocol/conformance), UCP spec 2026-04-08, SDK 0.4.6.

| Test file | Passed | Failed | Skipped |
|---|---|---|---|
| protocol_test | 2 | 0 | 1 |
| validation_test | 5 | 1 | 0 |
| idempotency_test | 4 | 0 | 0 |
| checkout_lifecycle_test | 7 | 4 | 0 |
| totals_test | 4 | 0 | 1 |
| fulfillment_test | 2 | 0 | 8 |
| fulfillment_structure_test | 2 | 0 | 0 |
| business_logic_test | 3 | 3 | 2 |
| discount_test | 0 | 0 | 6 |
| order_test | 0 | 4 | 0 |
| invalid_input_test | 1 | 2 | 0 |
| binding_test | 0 | 1 | 0 |
| ap2_test | 0 | 1 | 0 |
| card_credential_test | 0 | 1 | 0 |
| webhook_structure_test | 0 | 0 | 6 |
| webhook_test | 0 | 1 | 2 |
| simulation_url_security_test | 0 | 3 | 0 |
| **total** | **30** | **21** | **26** |

Failures and why:

- `ValidationTest.test_payment_failure`: needs a card payment instrument that can be declined; Etalase has no payment handlers
- `CheckoutLifecycleTest.test_cannot_cancel_completed_checkout`: same as test_complete_checkout: it cannot reach status completed through the card flow
- `CheckoutLifecycleTest.test_cannot_update_completed_checkout`: same as test_complete_checkout: it cannot reach status completed through the card flow
- `CheckoutLifecycleTest.test_complete_checkout`: asserts status completed straight after complete with a card instrument; with QRIS Etalase answers requires_escalation with a continue_url, and the checkout becomes completed only after the buyer pays on that page. The suite's buyer has only an email and it picks a flower-shop shipping option id (std-ship), while Etalase needs the buyer's name and phone number and offers courier options
- `CheckoutLifecycleTest.test_complete_is_idempotent`: same as test_complete_checkout: it cannot reach status completed through the card flow
- `BusinessLogicTest.test_buyer_consent`: buyer_consent capability not implemented (not advertised); the suite does not skip it
- `BusinessLogicTest.test_discount_flow`: discount capability not implemented (not advertised); the suite does not skip it
- `BusinessLogicTest.test_multiple_discounts_one_rejected`: discount capability not implemented (not advertised); the suite does not skip it
- `OrderTest.test_order_adjustments`: order capability not implemented (not advertised)
- `OrderTest.test_order_fulfillment_retrieval`: order capability not implemented (not advertised)
- `OrderTest.test_order_retrieval`: order capability not implemented (not advertised); also needs an order straight after complete
- `OrderTest.test_order_update`: order capability not implemented (not advertised)
- `InvalidInputTest.test_invalid_adjustment_status`: order capability not implemented (not advertised); needs a completed order
- `InvalidInputTest.test_malformed_adjustment_payload`: order capability not implemented (not advertised); needs a completed order
- `TokenBindingTest.test_token_binding_completion`: needs a card payment instrument that completes immediately; Etalase takes QRIS, which the buyer finishes on continue_url
- `Ap2MandateTest.test_ap2_mandate_completion`: AP2 mandates are not implemented (not advertised)
- `CardCredentialTest.test_card_credential_payment`: needs a card payment instrument that completes immediately; Etalase takes QRIS, which the buyer finishes on continue_url
- `WebhookTest.test_webhook_event_stream`: order webhooks not implemented; relies on the sample server's simulation hooks
- `SecurityTest.test_simulation_endpoint_correct_secret`: tests the sample server's /testing/simulate-* hooks, which a real business does not have
- `SecurityTest.test_simulation_endpoint_incorrect_secret`: tests the sample server's /testing/simulate-* hooks, which a real business does not have
- `SecurityTest.test_simulation_endpoint_missing_header`: tests the sample server's /testing/simulate-* hooks, which a real business does not have
