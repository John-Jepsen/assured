---
title: How to Make a Test Payment in the Demo
category: faq
---

# How to Make a Test Payment

This demo can process a payment against a policy balance without moving any real money. Payments run through a mock payment provider by default, and an optional test-mode adapter is available for a real card-processing sandbox. No live charges occur in either case, and no card numbers are stored, so you can practice the billing flow safely. The purpose is to show how a premium payment would be handled end to end, from verification through confirmation.

Because a payment touches sensitive account and money movement, it requires identity verification first. You will need to confirm your identity with at least two factors, including at least one strong factor such as a policy number, date of birth, or the one-time passcode, before the assistant will accept a payment. Once you are verified and the balance belongs to your session, the assistant can review the amount due and take the payment. Verification is enforced in application code, so this step cannot be skipped by the model.

The demo includes a simple, deterministic way to simulate a declined payment. Any payment amount that ends in ninety-nine cents, meaning the cents portion is .99, is treated as a decline by the mock provider. This lets you exercise the failure path on purpose and see how the assistant responds when a charge does not go through. Any other amount is treated as a successful test payment. Because the rule is fixed, you can reproduce a decline reliably whenever you want to test that branch.

When a payment succeeds, the assistant confirms the result and reflects the updated billing status. When a payment is declined, it explains that the charge did not go through and describes the usual next steps, such as trying a different amount or method, without exposing sensitive payment internals. In a real system a decline might prompt a retry after a short wait or a switch back to manual billing, and the demo mirrors that behavior at a high level. The assistant does not set fees or rates and will escalate policy-specific billing questions to a representative.

Nothing entered during a test payment represents a real financial transaction, and the demo does not retain full card or bank account numbers in chat transcripts. Sensitive values in a production system would be handled through secure, tokenized processing rather than plain text, and the demo preserves that separation of concerns. If you are unsure whether a test payment was recorded, the assistant can review your billing status again once your identity is verified.
