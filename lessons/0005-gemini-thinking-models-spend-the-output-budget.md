# 0005 — Gemini 3.x "thinking" models spend max_output_tokens before the answer

**Date:** 2026-09-14
**Area:** dashboard overlord (`dashboard/overlord.py`), voice answerer (`services/voice/answerer.py`)

## What happened
The first Overlord answer through `GenAiAnswerTransport` (the voice path's Gemini wrapper, `max_output_tokens=120`) came back as the fragment `"From scan 20"`. The voice budget of 120 tokens is fine for a one-sentence phone reply, but `gemini-3.8-flash` reasons before it writes, and that reasoning is billed against the same output budget — the visible text was what was left.

## Root cause
`max_output_tokens` caps thinking + answer together on thinking models. A budget sized for the answer alone truncates the answer.

## Fix
`dashboard/overlord.py::GenAiOverlordTransport` — its own transport with `max_output_tokens=1024`, same model resolution as the voice path. The voice answerer keeps 120 because its template fallback (Rule 6) catches a short/empty reply; if voice replies ever look clipped in a real call, raise that budget or disable thinking via `thinking_config` rather than reuse the number.

## How to avoid next time
Size `max_output_tokens` for thinking models at several times the expected answer length, and assert on the answer's ending (not just non-empty) when testing a new model id.
