"""One Gemini synthesis per paid scan (or recap per off-day) → validated SynthesisOutput."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pydantic import ValidationError

from ..packets import ContextProfile, SynthesisOutput, TrendPacket
from ..scrapers.free_trends import TrendSignal
from .gemini import GeminiLadder, GeminiResult
from .prompts import repair_prompt, synthesis_prompt, synthesis_system

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class SynthesisRejected(RuntimeError):
    def __init__(self, kind: str, detail: str, result: GeminiResult | None = None) -> None:
        super().__init__(f"synthesis rejected ({kind}): {detail}")
        self.kind = kind  # "malformed" | "unknown_post_id"
        self.detail = detail
        self.result = result


@dataclass
class SynthesisResult:
    output: SynthesisOutput
    model_used: str
    quality: str
    quality_warning: str | None
    repaired: bool = False


def parse_synthesis(text: str, allowed_ids: set[str]) -> SynthesisOutput:
    cleaned = _FENCE.sub("", text or "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise SynthesisRejected("malformed", "no JSON object found")
    try:
        data = json.loads(cleaned[start:end + 1])
        output = SynthesisOutput.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise SynthesisRejected("malformed", str(exc)[:300]) from exc
    unknown = [c.post_id for c in output.cards if c.post_id not in allowed_ids]
    if output.film_this and output.film_this.post_id not in allowed_ids:
        unknown.append(output.film_this.post_id)
    if unknown:
        raise SynthesisRejected("unknown_post_id", f"unknown post ids: {sorted(set(unknown))}")
    return output


def run_synthesis(ladder: GeminiLadder, context: ContextProfile, packets: list[TrendPacket], playbook: list[str],
                  *, trends: list[TrendSignal] | None = None, recap: bool = False) -> SynthesisResult:
    """Raises GeminiExhausted (ladder empty) or SynthesisRejected (after one repair on a lower rung)."""
    purpose = "recap" if recap else "synthesize"
    allowed = {p.post_id for p in packets}
    system = synthesis_system(context)
    prompt = synthesis_prompt(context, packets, playbook, trends, recap=recap)
    first = ladder.generate(purpose, system=system, prompt=prompt)
    try:
        output = parse_synthesis(first.text, allowed)
        return SynthesisResult(output, first.model_used, first.quality, first.quality_warning)
    except SynthesisRejected as rejection:
        repaired = ladder.generate(purpose, system=system,
                                   prompt=repair_prompt(prompt, first.text, rejection.detail),
                                   min_rung=first.rung_index + 1)
        try:
            output = parse_synthesis(repaired.text, allowed)
        except SynthesisRejected as second:
            second.result = repaired
            raise second
        return SynthesisResult(output, repaired.model_used, repaired.quality, repaired.quality_warning, repaired=True)
