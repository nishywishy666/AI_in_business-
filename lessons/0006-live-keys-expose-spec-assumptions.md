# 0006 — Live keys expose what the offline suites assumed

**Date:** 2026-09-14
**Area:** deploy / voice / marketing

## What happened
Checking the real keys before the Railway deploy found three failures no test could catch:
- Groq no longer serves `llama-3.1-8b-instant` (the model vr_plan.md V3 pins); the router would fail every live turn.
- The Google Calendar API was disabled in the Firebase project (`403 Google Calendar API has not been used in project …`), even with the calendar shared to the service account.
- The marketing backend only accepted `FIREBASE_CREDENTIALS_JSON` as a file path, which a host with env vars only (Railway) cannot provide.

## Root cause
Every suite runs on fakes, so model ids, enabled Google APIs and credential encodings were never exercised.

## Fix
- `GROQ_ROUTER_MODEL` default → `openai/gpt-oss-20b` (owner-approved; returns valid `json_object` routing output, ~0.5 s on a cold call).
- The Calendar API is enabled in the Cloud console for the project that owns `GOOGLE_SA_JSON`.
- `FirestoreBackend` accepts a path, raw JSON or base64 (`_load_credentials`, `tests/test_firestore_credentials.py`).

## How to avoid next time
Before any deploy, run a live smoke check per key: list Groq models and confirm the configured id, read one Calendar event, read one Firestore doc with the exact env value the host will get.

## Addendum — empty `FIRESTORE_EMULATOR_HOST=` breaks every Firestore call
`scripts/seed_business.py` failed with `Failed to create channel to '': the target uri is not valid`. The `.env` template carries `FIRESTORE_EMULATOR_HOST=` with no value; `load_dotenv` exports it as an empty string and the google client treats any set value as "use the emulator". Keep that line commented out in `.env`, and never add the variable (even empty) to Railway.
