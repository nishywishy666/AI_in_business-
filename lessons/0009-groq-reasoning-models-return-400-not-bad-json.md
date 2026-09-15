# 0009 — Groq reasoning models blow the token budget and return 400, not bad JSON

**Date:** 2026-09-14
**Area:** voice router (`services/voice/router.py`)

## What happened
With real keys, the router classified simple questions fine but dropped every booking request. The caller said "I'd like to book a table for four on Saturday at noon" and the call ended in a spurious callback: `router returned unparseable JSON 3x this call — prompt drift?`, then the `CALLBACK`/confidence 0.0 fallback.

## Root cause
`GROQ_ROUTER_MODEL=openai/gpt-oss-20b` is a reasoning model. `complete()` sent `max_tokens=200` with `response_format={"type": "json_object"}`. At default reasoning effort the model needs ~267 output tokens, spends the whole 200 on reasoning, and emits no JSON body — so Groq rejects the request with **HTTP 400 `json_validate_failed` and an empty `failed_generation`**. The longer the prompt, the likelier this is, which is why short factual questions survived and booking turns (longer system prompt, more slot state) did not. This is the Groq sibling of [0005](0005-gemini-thinking-models-spend-the-output-budget.md).

The failure was invisible because the repair loop catches the exception and falls back, so the logs blamed "unparseable JSON" — a *parse* error — when the API had actually returned a *400*. Nothing ever printed the status code.

## Fix
Send `reasoning_effort="low"` for `openai/gpt-oss*` models. Measured over 6 intents: 6/6 correct at 326 ms median, vs a hard failure before — and it is also the fastest option available on the account (`gpt-oss-120b` 506 ms, `groq/compound-mini` 1046 ms, `qwen/qwen3.6-27b` fails the same way).

## How to avoid next time
- A fallback that swallows the provider's exception type hides *which* thing broke. Log the exception class and status code before degrading.
- Before picking a router model, check whether it is a reasoning model; if it is, either cap reasoning effort or budget several times the JSON's length.
- Model availability is per-account: `llama-3.1-8b-instant` 404s on this key. List models against the actual key rather than assuming a catalogue.
