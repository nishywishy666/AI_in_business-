"""Gemini Flash phrasing (vr_plan.md §7.2, Rules 4 and 6).

Gemini's only job: turn one retrieved fact into one spoken reply. No tools, no database. On
timeout or error the caller gets the template fallback, never silence.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from config import threshold, thresholds

log = logging.getLogger(__name__)

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


class AnswerTransport(Protocol):
    model_id: str

    async def generate(self, *, system: str, prompt: str, timeout_s: float) -> str: ...


class GenAiAnswerTransport:
    """google-genai; model resolved at startup by intersecting the preference list with ListModels()."""

    def __init__(self, api_key: str, model_id: str, fallbacks: list[str] | None = None) -> None:
        self.api_key = api_key
        self.model_id = model_id
        self._fallbacks = list(fallbacks or [])
        self._client = None

    @classmethod
    def resolve(cls, api_key: str, preference: list[str] | None = None) -> "GenAiAnswerTransport":
        from google import genai

        client = genai.Client(api_key=api_key)
        available = {m.name.split("/")[-1] for m in client.models.list()}
        wanted = preference or thresholds()["GEMINI_MODEL_PREFERENCE"]
        usable = [m for m in wanted if m in available and "preview" not in m and "pro" not in m.split("-")]
        model_id = resolve_model(available, wanted)
        transport = cls(api_key, model_id, fallbacks=[m for m in usable if m != model_id])
        transport._client = client
        return transport

    def _step_down(self) -> bool:
        """The daily free quota is per model, so an exhausted rung must not strand every later turn."""
        if not self._fallbacks:
            return False
        previous, self.model_id = self.model_id, self._fallbacks.pop(0)
        log.warning("gemini quota exhausted, stepping down", extra={"from": previous, "to": self.model_id})
        return True

    def _get(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.api_key)
        return self._client

    async def generate(self, *, system: str, prompt: str, timeout_s: float) -> str:
        from google.genai import types

        config = types.GenerateContentConfig(system_instruction=system, temperature=0.4, max_output_tokens=120)
        try:
            response = await self._get().aio.models.generate_content(model=self.model_id, contents=prompt,
                                                                     config=config)
        except Exception as exc:
            # 429 = this model's daily free quota is gone for the rest of the Pacific day. This turn
            # still falls back to its template; every later turn uses the next model that has quota.
            if getattr(exc, "code", None) == 429 or "RESOURCE_EXHAUSTED" in str(exc):
                self._step_down()
            raise
        return response.text or ""


def resolve_model(available: set[str], preference: list[str]) -> str:
    for model_id in preference:
        if "preview" in model_id or "pro" in model_id.split("-"):
            continue
        if model_id in available:
            return model_id
    raise RuntimeError(f"no Gemini model from the preference list is available: {preference}")


class FakeAnswerTransport:
    model_id = "fake-gemini"

    def __init__(self, reply: str | None = None, *, delay_s: float = 0.0, error: Exception | None = None) -> None:
        self.reply, self.delay_s, self.error = reply, delay_s, error
        self.calls: list[dict] = []

    async def generate(self, *, system: str, prompt: str, timeout_s: float) -> str:
        self.calls.append({"system": system, "prompt": prompt})
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.error:
            raise self.error
        if self.reply is not None:
            return self.reply
        data = json.loads(prompt.split("FACT:", 1)[1].split("\n", 1)[0]) if "FACT:" in prompt else {}
        if "price" in data and data.get("price"):
            return f"The {data.get('name')} is {data['price']}."
        if "value" in data:
            return f"{data['value']}."
        if "allergens" in data:
            return f"For the {data.get('name')}, " + ", ".join(f"{a} {s}" for a, s in data["allergens"].items()) + "."
        return "Sure."


SYSTEM = """You are the voice of {business_name}, a restaurant, speaking to a caller on the phone. Turn the supplied fact into ONE spoken reply.
- One or two sentences, under {max_words} words. Spoken register: contractions, no bullet points, no markdown, no emoji. This text goes straight to text-to-speech.
- Use ONLY the supplied fact. If it does not answer the question, say so plainly; do not fill the gap.
- Never state a price, time, ingredient or allergen that is not in the supplied data.
- Never list more than three items.
- Do not add disclaimers; Python appends any that apply."""

CHITCHAT_SYSTEM = """You are the voice of {business_name}, a restaurant, on the phone. Reply with ONE short friendly line (under 15 words), spoken register. Do not state any fact about the business — no hours, prices, dishes, address."""


@dataclass
class Answer:
    text: str
    used_fallback: bool
    ms: int
    model_id: str | None
    warning: str | None = None


async def phrase(transport: AnswerTransport, *, fact: dict, question: str, history: list[dict], business_name: str,
                 fallback: str) -> Answer:
    system = SYSTEM.format(business_name=business_name, max_words=threshold("MAX_REPLY_WORDS"))
    recent = "\n".join(f"{h['role']}: {h['text']}" for h in history[-4:])
    prompt = f"FACT: {json.dumps(fact, ensure_ascii=False, default=str)}\nRECENT:\n{recent}\nCALLER ASKED: {question}\nReply:"
    return await _run(transport, system=system, prompt=prompt, fallback=fallback)


async def chitchat(transport: AnswerTransport, *, text: str, history: list[dict], business_name: str,
                   fallback: str) -> Answer:
    system = CHITCHAT_SYSTEM.format(business_name=business_name)
    recent = "\n".join(f"{h['role']}: {h['text']}" for h in history[-4:])
    prompt = f"RECENT:\n{recent}\nCALLER SAID: {text}\nReply:"
    return await _run(transport, system=system, prompt=prompt, fallback=fallback)


async def _run(transport: AnswerTransport, *, system: str, prompt: str, fallback: str) -> Answer:
    started = time.perf_counter()
    timeout_s = threshold("GEMINI_TIMEOUT_MS") / 1000
    try:
        text = await asyncio.wait_for(transport.generate(system=system, prompt=prompt, timeout_s=timeout_s), timeout_s)
        text = _clean(text)
        if not text:
            raise ValueError("empty reply")
    except Exception as exc:
        return Answer(fallback, True, int((time.perf_counter() - started) * 1000), getattr(transport, "model_id", None),
                      warning=f"gemini fallback: {type(exc).__name__}")
    warning = None
    words = text.split()
    if len(words) > threshold("MAX_REPLY_WORDS"):
        sentences = _SENTENCE.split(text)
        text = " ".join(sentences[:2])
        warning = f"reply trimmed from {len(words)} words"
    return Answer(text, False, int((time.perf_counter() - started) * 1000), getattr(transport, "model_id", None), warning)


def _clean(text: str) -> str:
    text = re.sub(r"[*_`#>]+", "", text or "")
    return " ".join(text.split()).strip()
