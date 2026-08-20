---
title: How to Verify Your Identity in the Demo
category: faq
---

# How to Verify Your Identity

Before the assistant can share account details or take any action on a policy, it must confirm who you are. Identity verification protects your information and prevents anyone from accessing an account that is not theirs. In this demo the check is deterministic, meaning the same answers always produce the same result, and it is enforced in application code rather than being decided by the language model. You will be asked for identifying information near the start of a conversation whenever the topic requires it.

Verification in this demo requires at least two matching factors, and at least one of them must be a strong factor. Strong factors are pieces of information that are hard for someone else to guess: your policy number, your date of birth, or a one-time passcode (OTP) sent for the session. Weak factors are lower-sensitivity details such as your last name or your ZIP code. A single weak factor is never enough on its own, so you might provide a last name plus a policy number, or a ZIP code plus a date of birth, to satisfy the two-factor rule with one strong factor included.

The one-time passcode is the most convenient strong factor because it proves you control the contact method on file. In this synthetic demo the OTP is always the fixed value 123456, so you can complete a passcode-based verification without a real phone or email. In a production system the code would be freshly generated and delivered to your registered phone number or email address, and it would expire after a short time. The demo keeps the value constant only so the flow can be exercised repeatedly without external services.

Once you are verified, the session binds to your specific customer record, and the assistant will only read or change data that belongs to you. This session binding prevents cross-customer access even if account identifiers for other customers are mentioned in the conversation. If you start a new session, you will need to verify again, because verification status does not carry over between sessions. This is a deliberate safeguard rather than an inconvenience.

If your details do not match, the assistant will not reveal whether a particular factor was wrong, since confirming or denying individual values would help an attacker guess. Instead it will let you try again with the correct combination of factors. If you cannot complete verification, the assistant can still answer general educational questions about insurance, and it can escalate you to a human representative who can help through other channels. Verification only gates account-specific reads and actions, not general information.
