"""Model provider abstractions (LLM / STT / TTS / Embedding) + factory.

Business logic depends only on the interfaces in ``base``. The concrete
implementation is chosen at runtime by configuration (see ``factory``), so the
system is never coupled to a single model or vendor.
"""
