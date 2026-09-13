# 0003 — Marketing Radar pass 2 (spec §16 steps 11–14)

**Status:** Done
**Date:** 2026-09-13

## Goal
Finish the v1 library surface on top of plan 0002: Like → transcript → 3 angles → filming guide + script (draft, 14-day expiry, save), the daily `expire_drafts` job, the Marketing-section Gemini chat with its six tools, overlord read helpers (`OVERLORD.md`), and framework-agnostic route handlers the parent can mount. Then write `handoff.md` for a senior-engineer review.

## Context
Plan 0002 delivered the scan → ScanBrief pipeline offline-first with 93 tests. The parent dashboard still does not exist, so routes are plain Python handlers (plus an optional FastAPI router that imports lazily); no server is started here (spec §2.1). Live keys are still absent, so everything stays behind the existing fakes: `FixtureTransport` gains the four Like-only transcript endpoints, `OfflineGeminiTransport` learns the `angles` / `script` / `chat` / `expand` purposes, and a `TranscriptProvider` protocol wraps `youtube-transcript-api`.

## Approach
- `agent/like.py` — `like_post()` (liked=true, credit-gated transcript per §12.3, one `angles` call) and `choose_angle()` (one `script` call → `scripts/{uuid}` draft, `expires_at = now + 14d`, post.script_id).
- `services/scripts.py` — save (status=saved, expires_at=null), get/list/delete.
- `jobs/expire_drafts.py` — delete `status==draft AND expires_at < now`; clear dangling `post.script_id`; prune `scrapeCache` older than 14 days. Wired into `scheduler.py` (replaces the placeholder).
- `agent/chat.py` — `ChatSession.send()`: JSON action protocol `{"reply", "tool"}`, tools `get_brief`, `get_post`, `rewrite_hook`, `captions` (exactly 3), `recommend_tomorrow`, `like_trend`; max 2 tool rounds; grounded in the cached brief; no scrape tool exists (extra scrapes are never triggered from chat in v1); messages persisted under `chat/{threadId}/messages`; `GeminiExhausted` → reply with the exact reset time.
- `services/overlord.py` + `OVERLORD.md` — `get_marketing_summary()` / `get_stats()` built only from `services.brief` / `services.stats` (AST test extended).
- `services/api.py` — `RadarApi` returning `(status, body)` for the eight spec routes; 404 on unknown ids, 503 on Gemini exhausted or backend failure; optional `fastapi_router()`.
- `cli.py` — `like`, `angle`, `save`, `scripts`, `chat`, `expire`, `overlord`.
- Tests: acceptance #11 (Like at remaining=15 → no transcript endpoint), #12 (draft >14d deleted, saved kept), #13 extended to overlord, chat tool loop, api status codes.
- `handoff.md` at repo root, written for a senior software engineer and their agent.

## Outcome
Built as planned on 2026-09-13; suite is 117 tests green. Offline CLI chain verified: scan → like (transcript credit-gated; YouTube via free provider) → angle → draft with 14-day expiry → save clears expiry → chat tools (`recommend_tomorrow`, `captions`, `like_trend`) → overlord summary citing the scan id → expire.

Notes:
- `ScanDeps` gained `youtube_transcripts` (a `TranscriptProvider`); `offline_transport` now routes the four Like-only endpoints; `OfflineGeminiTransport` dispatches on a `PURPOSE:` line in the system prompt.
- Acceptance #13 AST check now also covers `services/overlord.py` and `services/scripts.py`.
- FastAPI is optional (`fastapi_router` imports lazily); the parent can use `RadarApi` from any framework.
- `handoff.md` written at repo root for the senior-engineer review.

Follow-ups: live-key run + normalizer corrections; parent UI; Firebase rules deployment (see handoff.md §10).
