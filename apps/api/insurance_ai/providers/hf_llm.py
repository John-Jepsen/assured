"""HuggingFace transformers LLM adapter. Requires transformers + torch.

Runs a small open-source instruct model locally (e.g. Qwen2.5-0.5B/1.5B-Instruct,
Llama-3.2-1B-Instruct) with token streaming. Model id + device follow config and the
hardware profile. This is the "open-source models by default" path; the mock/local
providers cover CPU-only demos without a download.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from threading import Thread

from insurance_ai.config import Settings
from insurance_ai.providers.base import ChatMessage, LLMProvider


class HFTransformersLLM(LLMProvider):
    name = "huggingface"

    def __init__(self, settings: Settings) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self._torch = torch
        device = "cuda" if settings.hardware_profile in ("nvidia", "cloud-gpu") else "cpu"
        self.device = device
        self.tokenizer = AutoTokenizer.from_pretrained(
            settings.llm_model, token=settings.huggingface_token
        )
        self.model = AutoModelForCausalLM.from_pretrained(
            settings.llm_model,
            token=settings.huggingface_token,
            torch_dtype=torch.float16 if device == "cuda" else torch.float32,
        ).to(device)

    def _prompt(self, messages: list[ChatMessage]) -> str:
        chat = [{"role": m.role, "content": m.content} for m in messages]
        return self.tokenizer.apply_chat_template(
            chat, tokenize=False, add_generation_prompt=True
        )

    async def complete(self, messages: list[ChatMessage], **kwargs) -> str:
        return "".join([t async for t in self.stream(messages, **kwargs)])

    async def stream(self, messages: list[ChatMessage], **kwargs) -> AsyncIterator[str]:
        from transformers import TextIteratorStreamer

        prompt = self._prompt(messages)
        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.device)
        streamer = TextIteratorStreamer(
            self.tokenizer, skip_prompt=True, skip_special_tokens=True
        )
        gen_kwargs = dict(
            **inputs, streamer=streamer, max_new_tokens=kwargs.get("max_tokens", 400),
            do_sample=True, temperature=kwargs.get("temperature", 0.2),
        )
        thread = Thread(target=self.model.generate, kwargs=gen_kwargs)
        thread.start()
        loop = asyncio.get_event_loop()
        # Bridge the blocking streamer iterator into async.
        queue: asyncio.Queue = asyncio.Queue()

        def _drain():
            for tok in streamer:
                loop.call_soon_threadsafe(queue.put_nowait, tok)
            loop.call_soon_threadsafe(queue.put_nowait, None)

        Thread(target=_drain).start()
        while True:
            tok = await queue.get()
            if tok is None:
                break
            yield tok
