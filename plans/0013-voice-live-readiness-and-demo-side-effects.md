# 0013 — Voice pipeline against live keys, public-record facts, and a live-demo toggle

**Status:** Done
**Date:** 2026-09-14 (recorded 2026-09-15 while merging onto plan 0012 — this plan was written after the work, not before)

## Goal
Run the voice receptionist against real Groq, Gemini, Firestore, Google Calendar and Gmail before any
Twilio integration; fix what breaks; and make a demo possible without a paid Twilio number.

## What changed
- **Router** (`services/voice/router.py`): `reasoning_effort="low"` for `openai/gpt-oss*`. At the
  default effort the model spent `max_tokens=200` on reasoning and Groq returned 400
  `json_validate_failed`, which silently turned every booking request into a callback (lessons/0009).
- **Engine** (`services/voice/engine.py`): a deterministic yes/no mid-booking goes to the booking
  machine whatever the router labels it — "Yes that's correct" at the confirm step was classified
  `ANSWER_QUESTION` and the booking never committed (lessons/0010). `is_yes`/`is_no` are now public.
- **Facts** (`data/business/uncle_tony/facts.json`, Firestore): plan 0012's 27 facts, reconciled
  against public listings on the user's instruction. Corrected: halal (hedged "advertised as"
  phrasing, its own key), delivery (Uber Eats), owner/jobs email (admin@). Each carries
  `source: public_web` + `sourceUrl` + `confirmedByOwner: false`; facts that already matched carry
  `publicSourceUrl`. `scripts/pull_facts.py` copies Firestore → local, the missing direction.
- **Synonyms**: union of plan 0012's and this plan's lists; `halal` before `dietary`;
  `gift_vouchers`/`loyalty` before `coffee`/`payment`; no allergen wording in any fact (lessons/0011).
- **Templates** (`services/voice/tools.py`): a fact that is already a sentence is spoken as-is
  (was "Closed Sunday. on today." and "...directly..").
- **Answerer** (`services/voice/answerer.py`): steps down the preference list on a 429 instead of
  speaking templates for the rest of the day. Gemini 2.x ids 404 for new API users, so the list stays 3.x.
- **Marketing config**: `GEMINI_LADDER_JSON` holding a single object 500'd the whole dashboard;
  the parser now accepts a lone rung and errors clearly otherwise.
- **Live-demo toggle** (`services/booking/side_effects.py`, `/sim/side-effects`, `sim.html`): off by
  default; when on, a local-sink booking sends the real confirmation email and writes the real
  Calendar event. The booking row stays in `.testruns` (no seat consumed, no Firestore write) and
  `api/voice/ws.py` is untouched, as `tests/test_sim_isolation.py` requires.

## Verified live
Full 9-turn booking → Firestore booking `confirmed`, seats decremented, Calendar event on the
owner's calendar, confirmation email delivered. The same through `/sim` with the toggle on and off.

## Still open
- Voice replies are template-phrased in practice: Gemini 3 needs ~2.8 s for a complete answer against
  the 1.2 s phone budget (`GEMINI_TIMEOUT_MS`), and truncates at `max_output_tokens=120` (lessons/0005).
- Firestore and Gemini free-tier daily quotas were exhausted during testing.
- No ONNX models in `./models`: turn-taking uses the energy VAD + silence fallback.
- Public-record facts are unconfirmed by the owner; halal certification is explicitly not verified.
