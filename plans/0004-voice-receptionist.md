# 0004 — Voice AI Receptionist (spec: plans/voice-receptionist/vr_plan.md)

**Status:** Done (pytest gates); real-call gates deferred to docs/setup-checklist.md
**Date:** 2026-09-13

## Goal
Build the Twilio-answering AI receptionist plus its local simulator exactly as `plans/voice-receptionist/vr_plan.md` specifies, in its five phases with gates (R1), without touching `marketing_radar/` (R2).

## Context
The spec is a locked build contract with its own rules (R0–R7, V1–V12). Reconciled against this repo on 2026-09-13:

- **Nothing the spec lists as "already existing" is in this repo** — no `services/common/*`, no `businesses/{businessId}` Firestore tree, no dashboard. `services/common/*` is created here to the §11/§14 shapes as the spec allows. The Firestore tree and business data are an open question (see plan questions below).
- **Two Firestore trees will coexist.** The marketing agent (built to its own spec) writes `users/{uid}/marketingRadar/**`; the voice agent writes `businesses/{businessId}/…` per §11. Not reconciled — R2 forbids changing the marketing tree and R0 forbids inventing a mapping.
- **Python:** repo venv is 3.11; Vercel's Python runtime is 3.12. μ-law and resampling are implemented with a numpy lookup table + `scipy.signal.resample_poly` (one of the two options §6.4 offers) so the code is identical on every version and never depends on `audioop`.
- **App assembly:** §5 lists route modules but no ASGI entry. Vercel treats each `api/*.py` file as its own function, so a single FastAPI app at `api/index.py` mounting the routers, plus `vercel.json` rewrites, is required to run at all. Added as the one file outside §5's list.
- **Dependencies** go in an optional extra `voice` in `pyproject.toml` plus `requirements.txt` for Vercel, so the marketing install is unchanged.

## Approach
Phase order per §13. Gates that need a real phone call (Phase 1, 4, 5) are tracked in `docs/setup-checklist.md`; unit/simulator gates run in `uv run pytest`.

1. Phase 0 (this commit): `contracts/voice.py`, `config/*`, `services/common/*`, docs, env vars, plan.
2. Phase 1: `api/voice/{incoming,ws,status,fallback}.py`, `api/index.py`, `services/voice/{twiml,signature,audio,stream}.py`, `scripts/{render_greeting.py,warm.sh,tunnel.sh}`, `vercel.json`, tests `test_signature.py`, `test_audio.py`, `test_twilio_stream.py`.
3. Phase 2: `api/sim/app.py`, `/sim` page, `services/voice/sinks.py`, `test_sim_isolation.py`, `scripts/replay.py`.
4. Phase 3: `router.py`, `answerer.py`, `tools.py`, `templates.py`, `telemetry.py`, `tests/conversations/*.yaml`, `test_router.py`, `test_grounding.py`.
5. Phase 4: `booking_machine.py`, `email_capture.py`, `services/booking/capacity.py`, commit transaction, Calendar mirror, email, `scripts/seed_business.py`, `test_booking_*`, `test_email_capture.py`.
6. Phase 5: 280 s cutover, rollups, cost accounting, status callbacks.

## Open questions (R0 — not chosen unilaterally)
- `TODO(spec): config/capacity.yaml` — service windows and seat counts are not in the spec.
- `TODO(spec): business data` — no menu items, facts, allergen maps, or `BUSINESS_ID` are given; the simulator "must read the real menu and facts or it tests nothing".
- `TODO(spec): Pipecat` — §5 names Pipecat for `pipeline.py`, while `audio.py`/`stream.py` hand-roll what Pipecat's Twilio serializer does. Which one owns the loop?
- Credentials for the real-call gates (Twilio, ElevenLabs, Groq, Gemini, Firebase SA, Calendar SA, email).

## Outcome
All five phases built on 2026-09-13 in order, each phase's pytest gate green before the next started; `uv run pytest` runs the whole repo (marketing + voice). The marketing agent's suites are untouched and still pass (R2).

**User decisions during the build:** no credentials yet → offline build gated on unit/simulator tests; business data (menu, facts, capacity) is owned by the dashboard teammate → the suites use clearly marked TEST-ONLY documents in `tests/fixtures/business/`; hand-rolled asyncio media loop on onnxruntime instead of Pipecat.

**Files beyond §5's list (each justified in its docstring):** `api/index.py` (single ASGI entry), `services/voice/engine.py` (the per-turn brain shared by phone and Mode A), `services/voice/call_pipeline.py` (the production media loop), `services/voice/providers.py` (VAD/turn/STT/TTS behind protocols + fakes), `services/booking/{capacity,commit,calendar,email}.py`, `config/{fact_synonyms,pricing}.yaml`, `api/sim/sim.html`, `tests/voice_helpers.py`, `tests/test_voice_config.py`, `tests/test_sim.py`, `tests/test_call_pipeline.py`.

**Deviations from the letter of the spec (all flagged in code):**
- A fifth `<Parameter name="from">` on the `<Stream>` so the booking machine can offer caller ID without an extra Firestore round trip (§6.1 lists four).
- Question → lookup mapping (§10 leaves it to "Python") lives in `config/fact_synonyms.yaml`.
- Unknown `post`-style gaps in wording: every unworded utterance is a `TODO(spec)` template in `services/voice/templates.py`.
- STT is one Scribe request per caller turn (after end-of-turn) rather than a verified realtime websocket; partials are therefore coarse. Smart Turn v3.2 input features are unverified; the silence rule is the fallback when the ONNX file is absent.
- `costCents` is always 0 until `config/pricing.yaml` is filled (R0 forbids inventing prices).
- Phonetic confirmation of confusable letters is one combined question after the caller spells, not letter-by-letter.

**Still open (see handoff.md → Voice receptionist):** all `TODO(spec)` markers, the real-call gates, ONNX model files under `./models`, the exact context of `businesses/{businessId}` data, Firebase indexes and rules, and the two-tree Firestore split with the marketing agent.
