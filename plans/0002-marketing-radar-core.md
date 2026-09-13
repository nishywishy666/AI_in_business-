# 0002 — Marketing Radar core pipeline (spec §16 steps 1–10)

**Status:** Done
**Date:** 2026-09-13

## Goal
Build the `marketing_radar` Python library described in `marketing_agent_plans/Trend-Radar-Technical-Spec.md` up to the point where a 2-day scan produces a Gemini-synthesized ScanBrief packet plus a usage snapshot and alerts — offline-first, fully tested, zero credits spent.

## Context
The hackathon web-app has three parts (Voice AI receptionist, dashboard, marketing agent). The marketing analysis agent pipeline comes first; the other two are deferred. The spec is a locked build contract. The only prior code to port is the scoring math in `previous_work/.../tools/idea-scout/score-ideas.py` (relative velocity, recency, dedup — minus the Higgsfield term). No live Firebase / ScrapeCreators / Gemini credentials exist yet, so every external dependency is built behind an injectable backend/transport with in-memory fakes and JSON fixtures; real clients activate when `.env` keys are present.

## Approach
- Python 3.11, `uv`, `pyproject.toml`, pytest. Package at repo-root `marketing_radar/`, tests in `tests/`.
- Firestore layout = spec §6.1 recommended layout, encoded only in `db/paths.py`; `RadarStore` raises `NamespaceViolation` on any write outside `users/{uid}/marketingRadar/`.
- Injectable `clock` everywhere for deterministic 48h-cache / 14-day-recency / midnight-Pacific tests.
- Gemini via `google-genai` behind a `GeminiTransport` protocol; ScrapeCreators + free APIs behind an `HttpTransport` protocol with a `FixtureTransport` for offline runs.
- Dev harness `python -m marketing_radar.cli --offline {daily-pull,scan,recap,brief,stats}` (a CLI, not a server).
- Build order: namespace client → schema/cache/daily pull → context parser → ScrapeCreators client → free adapters → scoring → scan rotation/shedding → Gemini ladder → synthesis/ScanBrief → usage snapshot/alerts + services + scheduler + CLI.
- Deferred to plan 0003: Like → angles → script, draft expiry, chat tools, parent routes/UI, `OVERLORD.md`, live-key run.

## Outcome
Built as planned on 2026-09-13. `marketing_radar/` ships steps 1–10 with 93 passing tests (`uv run pytest`), including spec §17 acceptance tests #1–#10, #13 and #14 as named tests. The offline CLI runs a paid scan → ScanBrief, four successive scans rotate Call 3 per the table with 48h cache hits, a zero-credit run falls through to the off-day recap, and `stats` returns the §13 snapshot with alerts.

Deviations / notes:
- `usage/snapshot` is `users/{uid}/marketingRadar/usage` (a doc) with `events` and `geminiDaily` subcollections; lifecycle collections hang off the `meta` marker doc. All encoded in `db/paths.py` only.
- Synthesis with an unknown `post_id` gets one repair on a lower rung, then the brief is *not* written (posts are still persisted, alert `synthesis_warning` raised). Malformed JSON degrades to a ranked brief without `weekly_take`.
- `services/stats.py` and `services/brief.py` import nothing from `scrapers` or `agent` (AST-checked).
- ScrapeCreators response shapes are guessed; see CLAUDE.md "Unverified assumption".

Follow-ups → plan 0003: Like → angles → script, `expire_drafts`, chat tools, `OVERLORD.md`, parent route wrappers, live-key run.
