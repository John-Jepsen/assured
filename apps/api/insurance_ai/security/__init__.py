"""Security: deterministic identity verification + tool authorization.

These controls live in application code, never in the LLM. The model may *request*
a protected action; whether it executes is decided here.
"""
