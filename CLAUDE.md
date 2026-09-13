# AI in Business — Hackathon Project

This repo is the main codebase for the **AI for Businesses Hackathon**. The product is an agentic business dashboard with three parts: a **marketing analysis agent** (built first — see below), a Voice AI receptionist, and the parent dashboard/overlord (both deferred). This file governs process; the build contracts live in `marketing_agent_plans/`.

## Working agreement

Two folders exist specifically to keep this hackathon coherent across many short, fast-moving sessions:

- **[plans/](plans/)** — one file per non-trivial piece of work, written *before* starting it.
- **[lessons/](lessons/)** — one file per mistake, bug, or surprising failure, written *after* hitting it.

Both are living folders. Update them as you go, not just at the end of a session — see each folder's README for the exact convention and template.

### When to write a plan
Before starting any non-trivial change (new feature, refactor, architecture decision, dependency choice). Skip it for trivial one-line fixes or pure exploration. See [plans/README.md](plans/README.md).

### When to write a lesson
Immediately after resolving any bug, broken build, failed deploy, wrong assumption, or anything that cost real time to figure out — while the root cause is still fresh. See [lessons/README.md](lessons/README.md).

### Before starting new work
Skim [lessons/](lessons/) for anything relevant to the area you're about to touch, so past mistakes aren't repeated.

## Conventions
- Plans and lessons are numbered sequentially (`0001-`, `0002-`, ...) and named with a short kebab-case slug.
- Keep entries short and concrete — a paragraph or a few bullets, not an essay.
- Do not delete old plans/lessons; if a plan changes direction, update its **Status** field and note why rather than rewriting history.

## Marketing agent (`marketing_radar/`)

- **Spec (source of truth):** [marketing_agent_plans/Trend-Radar-Technical-Spec.md](marketing_agent_plans/Trend-Radar-Technical-Spec.md). The `.docx.md` copy is identical; `Budget-Trend-Radar-Plan.docx.md` is the product overview. If a chat message conflicts with the spec, the spec wins unless the user overrides a numbered constraint.
- **Shape:** an importable Python 3.11 library the parent dashboard starts (`start_marketing_radar(user_id)`), not a server. Firestore writes only under `users/{uid}/marketingRadar/**`; all AI is Gemini free tier; ScrapeCreators budget is 100 credits, ≤3 live calls per 2-day scan, 15-credit reserve. No Anthropic key anywhere in this module.
- **Offline-first:** every external dependency sits behind an injectable backend/transport with fakes + fixtures in `tests/fixtures/`. `python -m marketing_radar.cli scan --offline --user-id demo` runs the whole pipeline with zero credits.
- **Reference only:** `previous_work/` is the earlier Editoz AIOS repo. Only `tools/idea-scout/score-ideas.py` (scoring math) was ported; do not clone its video/Telegram/Claude Code machinery.
- **Tooling:** `uv sync --extra dev`, `uv run pytest`. Acceptance tests from spec §17 are named `test_acceptance_N_*`.
- **Handoff / integration docs:** [handoff.md](handoff.md) (review guide, decisions, unverified assumptions), [OVERLORD.md](OVERLORD.md) (read-only helpers for the main agent).
- **Unverified assumption:** ScrapeCreators response field names in `tests/fixtures/scrapecreators/` are best-effort guesses; the first live scan must be used to correct `scoring/normalize.py`.

## Voice AI receptionist (`api/`, `services/voice/`, `services/booking/`, `config/`, `contracts/`)

- **Spec (source of truth):** [plans/voice-receptionist/vr_plan.md](plans/voice-receptionist/vr_plan.md). Its rules R0–R7 apply to any change here: never invent a requirement (emit `TODO(spec)`), build in phase order, do not touch the marketing agent, no secrets client-side, arithmetic in Python never in a model, Pydantic at every model boundary, `call_id` on every log line, the simulator drives the production code path.
- **Shape:** a FastAPI app (`api/index.py`) answering Twilio Media Streams; `services/voice/engine.py` is the per-turn brain shared by the phone path and the simulator's Mode A; `services/voice/sinks.py` is the structural write isolation (`LocalJsonlSink` has no Firestore client).
- **Run:** `ENABLE_SIM=1 SESSION_SINK=local uv run uvicorn api.index:app --port 8000` then open `/sim`. Real calls need the keys in `.env.example` plus `scripts/tunnel.sh`; see [docs/setup-checklist.md](docs/setup-checklist.md).
- **Two Firestore trees coexist:** marketing writes `users/{uid}/marketingRadar/**`; voice writes `businesses/{businessId}/**`. Not reconciled (R2 + R0) — see plan 0004.
- **Open items:** every `TODO(spec)` in the tree (`grep -rn "TODO(spec)"`), the real-call gates, ONNX model files, and the business dataset owned by the dashboard team.

## Status
- [x] Repo scaffolding (CLAUDE.md, plans/, lessons/)
- [x] App stack decided — Python 3.11 library for the marketing agent; dashboard/voice stack still open
- [x] Marketing agent core pipeline — plan 0002 (spec §16 steps 1–10)
- [x] Marketing agent pass 2 — Like → script, draft expiry, chat tools, overlord helpers, route handlers (plan 0003)
- [x] `handoff.md` for senior-engineer review (read it first if you are new here)
- [ ] Live-key end-to-end scan (needs Firebase service account + ScrapeCreators + Gemini keys)
- [ ] Dashboard / overlord
- [x] Voice AI receptionist — plan 0004, phases 1–5 built offline; pytest gates green
- [ ] Voice receptionist real-call gates (Twilio number, ElevenLabs greeting render, Calendar, email) — docs/setup-checklist.md
