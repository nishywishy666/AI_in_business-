# 0005 — Dashboard wiring: voice receptionist + marketing agent behind the Uncle Tony UI

**Status:** Done (offline + pytest); Vercel deploy still to run
**Date:** 2026-09-14

## Goal
Serve the Claude Design export in `UI/` as the product's web app, fed live by the voice receptionist (`api/`, `services/`) and the marketing agent (`marketing_radar/`), without editing any file under `UI/`.

## Context
- `UI/UI mockups project scope/Uncle Tony Overlord Dashboard.dc.html` is a single template rendered by the vendored `support.js` runtime. Every list and number is hard-coded inside its `<script data-dc-script>` class; there are no fetch calls. `uploads/metrics.md` is the metric contract for the Analytics screen.
- User decisions: (1) serve-time injection, UI files byte-identical; (2) the Uncle Tony details in the mockup become the business dataset (menu, facts, hours, 40 seats) — **owner-unverified**; (3) Vercel is the deploy target (Firestore store, Vercel Cron for marketing jobs); the local uvicorn path stays for the simulator, tests and the offline demo.
- `support.js` evaluates the script block with `new Function(..., src + ";return Component")`, so text appended inside that tag runs after the class with `Component` in scope; `componentDidMount()` is called by the host and `renderVals()` reads instance fields on every render.

## Approach
1. `dashboard/` package: `records` (normalised entities + `LocalJsonlSource` / `FirestoreSource`), `analytics` (metrics.md formulas, pure), `present` (entities → the exact shapes the template binds), `routes` (JSON API under `/api/dashboard`, `/api/overlord`, `/api/jobs`), `marketing` (ScanDeps + trend cards), `overlord` (grounded Gemini answer with template fallback), `ui` (page assembly: literal→binding patches + `bridge.js` appended inside the script tag, static files from the UI folder), `app.mount_dashboard` called from `api/index.py::create_app`.
2. Marketing router mounted at `/api/marketing`; `MARKETING_USER_ID` defaults to `BUSINESS_ID` (the two-tree split plan 0004 left open — the dashboard owns the mapping); questionnaire seeded from `data/business/uncle_tony/marketing_context.json`.
3. Business dataset `data/business/uncle_tony/`, `config/capacity.yaml` filled from the mockup hours, `JsonBusinessReader` so the local engine answers from it, `scripts/seed_business.py` seeds menu + facts + context, `scripts/seed_demo_calls.py` drives the real engine with scripted router output to produce a day of calls.
4. `services/common/config.py`: relaxed required set when `SESSION_SINK=local` so the dashboard runs with zero keys.
5. Vercel: catch-all rewrites to `api/index`, `includeFiles`, `crons` → `/api/jobs/*` with `CRON_SECRET`.

Mapping decisions (route/outcome labels, task-result grading, latency proxy, handoff classes, trend score normalisation, Like/Save semantics) are documented in `dashboard/present.py` / `dashboard/analytics.py` docstrings.

## Outcome
Built on 2026-09-14 as planned. `UI/` is byte-identical (hashes pinned in `tests/fixtures/ui_manifest.json`); the served page passes the runtime's own evaluation path (checked in node against real API payloads: every screen renders, Like → angles modal, Save, chat, Overlord, Mark done, Approve, period switch, toggles all hit the API). `uv run pytest` is green across marketing, voice and the five new dashboard suites; `uvicorn api.index:app` boots with zero keys and serves `/`, the assets, `/api/dashboard/*`, `/api/marketing/*`, `/api/overlord/ask`, `/api/jobs/*` and `/sim`.

Deviations from the plan:
- `test_dashboard_ui.py` pins the UI export by hash instead of diffing against git (the folder is untracked); re-exporting the design means regenerating the manifest and re-checking the `PATCHES` anchors.
- The Overlord got its own Gemini transport (lesson 0005) instead of reusing the voice answerer.
- Grading: a call with a knowledge gap grades as a failure even when a callback was logged; callbacks without a gap are "unknown". A question that ended in a callback keeps route Question with outcome "Couldn't answer" (the mockup's row), Callback is reserved for the explicit ask for a person.
- The booking machine has no "no email, thanks" path (TODO(spec)), so every demo booking supplies an email.
- Extra: `local_config()` so `SESSION_SINK=local` needs no env at all; audio providers load only when ElevenLabs is configured.

Still open: Vercel deploy (`includeFiles`, rewrite order, cron GETs untested live), owner sign-off on the Uncle Tony dataset and capacity, `config/pricing.yaml` so costs stop reading 0, Firestore rules for the three dashboard writes.
