# Setup checklist (vr_plan.md §15) — the things that fail at the worst moment

**Google Calendar — most likely to be silently broken**
1. Create a service account, base64 its JSON into `GOOGLE_SA_JSON`.
2. **In the owner's Google Calendar, share the calendar with the service account's email address and grant "Make changes to events."** A service account gets its own empty calendar the owner cannot see. Without this step bookings land nowhere and nothing errors. Domain-wide delegation is the usual fix and needs Google Workspace, which an independent toastie shop does not have.
3. Verify by looking at the owner's own calendar UI, not an API response.

**Twilio**
4. Upgrade off the trial account — a trial plays Twilio's own message before your greeting and only accepts verified callers.
5. Number's "A call comes in" → `POST {PUBLIC_BASE_URL}/api/voice/incoming`; "Primary handler fails" → `/api/voice/fallback`.
6. Buy the AU mobile number early; AU KYC is tightening.
7. For local testing: start the tunnel (`scripts/tunnel.sh`), then set `PUBLIC_BASE_URL` to the tunnel origin **before** starting the app, or signature validation fails on every request.

**Firebase**
8. Deploy every composite index before any demo. A missing one fails the query at runtime and the caller hears silence.
9. Confirm `FIRESTORE_EMULATOR_HOST` is not set in Vercel's environment.
10. `scripts/seed_business.py` has materialised `capacitySlots` for the next 60 days.

**Audio models (ONNX, never torch — vr_plan.md §3)**
14. Put `silero_vad.onnx` and `smart-turn-v3.2.onnx` under `./models/` (gitignored). Without them the pipeline logs an error at boot and falls back to an energy VAD + a silence rule — fine for the simulator, not for judged calls. TODO(spec): the spec does not say where to fetch them.
15. `uv run python scripts/render_greeting.py --business-name "..."` so `assets/voice/greeting.ulaw` exists; a missing greeting is logged loudly and the call proceeds with no disclosure audio.

**Demo day**
11. `scripts/warm.sh` immediately before presenting — cold start lands on the first caller.
12. Place one real test call end to end, including the email arriving.
13. Check that at least one `rollups/` document exists so the dashboard is not empty on screen.

## Real-call acceptance gates (vr_plan.md §13)
These cannot run in `pytest`; tick them off on a real phone.
- [ ] Phase 1 — a call plays the disclosure greeting, then echoes the caller's audio back; `syd1` appears in Vercel function logs.
- [ ] Phase 4 — a call books a table; the event appears in the owner's Google Calendar; the email arrives; killing Calendar credentials does not fail the booking.
- [ ] Phase 5 — a call held past 280 s continues mid-booking with one acknowledging line; the disclosure plays exactly once across the cutover; a mid-turn hangup sets `outcome='abandoned'` from the status callback.
