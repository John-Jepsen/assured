---
title: What the Assistant Can and Cannot Do
category: faq
---

# What the Assistant Can and Cannot Do

The assistant is designed to handle common customer-service tasks across several insurance topics, including policy questions, claims, billing, account updates, and appointment scheduling. It can explain how coverages work, look up the status of your policy or an existing claim once you are verified, help you start a claim, review your billing balance and due dates, and take a test payment in this demo environment. It routes each request to a specialist area using a deterministic intent router, so a billing question reaches billing handling and a claims question reaches claims handling, and a single message can trigger more than one area when it covers multiple topics.

An important design principle is that the assistant only phrases facts that have already been verified by application code. When it tells you a policy limit, a due date, or a claim status, that value comes from the underlying records and tool results, not from the model's own guesses. The language model composes the wording of the answer, but it does not invent policy data, and the selection and authorization of any tool are handled deterministically outside the model. This is why the assistant can be trusted to report your account details accurately rather than hallucinating coverage that does not exist.

There are clear limits on what the assistant will do. It will not disclose account-specific information or make any change until your identity is verified and the account is confirmed to be yours. It does not set premiums, rates, or fees, and it does not make underwriting or coverage-eligibility decisions, because those require a licensed representative. It does not store full card or bank account numbers, and it does not provide legal, tax, or medical advice. When a request falls outside these boundaries, the assistant says so plainly instead of improvising an answer.

The assistant is also honest about the limits of its knowledge. It answers educational questions using a curated knowledge base and cites the sources it drew from. When the knowledge base does not contain enough information to answer safely, it tells you that it does not have enough information rather than inventing a response, and it offers to escalate. It never fabricates coverage, exclusions, or policy terms to fill a gap, which is a deliberate safety choice.

For anything the assistant cannot handle, escalation to a human is always available. Certain situations, such as a disputed claim, a complaint, or a request that requires professional judgment, are routed to a person by design. You can also ask for a human at any point in the conversation. The goal is to resolve straightforward requests quickly through the assistant while making sure that sensitive or complex matters reach someone qualified to handle them.
