# Handoff — AI in Business hackathon repo

**For:** a senior software engineer reviewing this work, and the coding agent they will point at it.
**State on 2026-09-14:** three subsystems built offline-first with zero live credentials: the **marketing analysis agent** (`marketing_radar/`), the **voice AI receptionist** (`api/`, `services/`, `config/`, `contracts/`) and the **dashboard / overlord** (`dashboard/`, plan 0005) that serves the Claude Design export in `UI/` untouched and feeds it from the other two. `uv run pytest` runs everything. Part 1 below is the marketing agent, **Part 2 is the voice receptionist**, **Part 3 (bottom) is the dashboard**.

---

# Part 1 — Marketing Radar (marketing analysis agent)

---

## 1. Read these first (in this order)

| What | Where | Why it matters |
|---|---|---|
| Working agreement for this repo | [CLAUDE.md](CLAUDE.md) | Plans before work, lessons after bugs, spec wins over chat |
| **Build contract (source of truth)** | [marketing_agent_plans/Trend-Radar-Technical-Spec.md](marketing_agent_plans/Trend-Radar-Technical-Spec.md) | Every numbered constraint the code obeys. `Trend-Radar-Technical-Spec.docx.md` is an identical copy |
| Product overview | [marketing_agent_plans/Budget-Trend-Radar-Plan.docx.md](marketing_agent_plans/Budget-Trend-Radar-Plan.docx.md) | Why the module exists, who owns what |
| Plan for pass 1 (steps 1–10) + outcome | [plans/0002-marketing-radar-core.md](plans/0002-marketing-radar-core.md) | Decisions the spec left open, deviations |
| Plan for pass 2 (steps 11–14) + outcome | [plans/0003-marketing-radar-pass2.md](plans/0003-marketing-radar-pass2.md) | Like/script, chat, overlord, routes |
| Repo scaffolding plan | [plans/0001-repo-setup.md](plans/0001-repo-setup.md) | Process only |
| Lessons (mistakes already made) | [lessons/0001-firestore-context-path-is-a-collection.md](lessons/0001-firestore-context-path-is-a-collection.md), [lessons/0002-argparse-subparser-defaults-override-globals.md](lessons/0002-argparse-subparser-defaults-override-globals.md) | Don't repeat them |
| Overlord integration | [OVERLORD.md](OVERLORD.md) | How the main agent reads marketing data without scraping |
| Quick start | [README.md](README.md) | Commands |
| Conventions for the two process folders | [plans/README.md](plans/README.md), [lessons/README.md](lessons/README.md) | Templates: [plans/TEMPLATE.md](plans/TEMPLATE.md), [lessons/TEMPLATE.md](lessons/TEMPLATE.md) |

`previous_work/` is the earlier Editoz AIOS repo, **reference only**. The only thing ported is the scoring math in `previous_work/Acceleratoz-aios-feat-portable-install/tools/idea-scout/score-ideas.py` (relative velocity, recency, dedup — Higgsfield term dropped). Nothing else from it should be pulled in (spec §2.10, §19).

## 2. What this is

A Python 3.11 **library** (`marketing_radar/`) the parent dashboard imports. Once a day it reads the parent's questionnaire from Firestore, every 2 days (or whenever the owner clicks Refresh now) it scrapes TikTok + Instagram + Facebook + YouTube Shorts via ScrapeCreators (4 credits per pull — plan 0013 overrode the spec's 3-call cap; 100-credit lifetime budget, 15-credit reserve for transcripts), always adds free YouTube/Reddit/Trends, scores with the AIOS formula, makes **one** Gemini free-tier call to write a `ScanBrief`, and exposes Like → 3 angles → script, a grounded chat, usage snapshot + alerts, and read-only overlord helpers. All writes land under `users/{uid}/marketingRadar/**`; nothing else in Firebase is touched. No Anthropic key anywhere.

## 3. How to run

```bash
uv sync --extra dev
uv run pytest                                   # whole repo: 296 tests (117 marketing + voice), ~5s, no network

c() { uv run python -m marketing_radar.cli --offline --user-id demo "$@"; }
c reset; c scan; c brief; c stats               # paid scan → ScanBrief, usage snapshot
c scan; c scan; c scan                          # watch Call 3 rotate FB → YT → FB-group → YT, cache hits
c recap                                         # off-day recap, 0 credits
c like <post_id>; c angle <post_id> 1; c save <script_id>; c scripts
c chat "what should I post tomorrow?"; c chat "captions please" --post-id <post_id>
c overlord; c expire
```

Offline mode = JSON-file Firestore stand-in under `.marketing_radar_cache/` + fixtures in `tests/fixtures/` + a deterministic Gemini fake. Live mode = copy `.env.example` → `.env`, fill keys, drop `--offline`. The parent wires `from marketing_radar.scheduler import start_marketing_radar; start_marketing_radar(user_id)` and may mount `marketing_radar.services.api.RadarApi` (or `fastapi_router(api)`).

## 4. Package map

```
marketing_radar/
  config.py, clock.py            Settings from env (ladder, reserve=15, 48h), injectable clock, Pacific-day helpers
  db/paths.py                    THE ONLY place that knows the Firestore layout
  db/firebase.py                 RadarStore: raises NamespaceViolation on writes outside the prefix
  db/backend.py|firestore_backend.py|json_backend.py   Memory / real / JSON-file backends
  cache/local.py                 platformdirs cache: context.json, brief.json, brief_parts/, stats.json, meta.json
  context/parse.py               questionnaire (Firestore map or context.md) → ContextProfile
  packets/schema.py              pydantic v2 contracts (ScanBrief, TrendPacket, Script, UsageSnapshot, ...)
  scrapers/endpoints.py          frozen ScrapeCreators allowlist + forbidden params
  scrapers/client.py             48h request cache, credit accounting, balance only before a scan
  scrapers/transport.py          HttpTransport protocol: httpx | FixtureTransport
  scrapers/free_*.py, transcripts.py   YouTube Data API, Reddit, pytrends, youtube-transcript-api (all behind protocols)
  scoring/normalize.py|niche_fit.py|score.py   raw payload → TrendPacket → AIOS relative score → lists
  agent/gemini.py                ladder: best rung first, 429/cap → step down once, Pacific-day counters
  agent/prompts.py|synthesis.py  JSON-only prompts; one repair on a lower rung; unknown post_id → rejected
  agent/like.py|chat.py          Like → transcript (credit-gated) → angles → script; chat with 6 tools, no scrape tool
  jobs/daily_pull.py|scan.py|expire_drafts.py   the three scheduled jobs
  usage/snapshot.py|alerts.py    §13 snapshot, one alert per (provider, severity), cleared on recovery
  services/brief.py|stats.py|scripts.py|overlord.py|api.py   read helpers, script lifecycle, route handlers
  scheduler.py, deps.py, cli.py  APScheduler registration, dependency wiring (real vs fake), dev harness
tests/                           one file per layer; fixtures/ holds recorded-shape JSON
```

## 5. Spec acceptance tests (§17) → test names

| # | Requirement | Test |
|---|---|---|
| 1 | write outside namespace raises | `test_paths_firebase.py::test_acceptance_1_*` |
| 2 | daily pull no-op / torn cache refetch | `test_cache_daily_pull.py::test_acceptance_2_*` |
| 3 | one live call per platform (4 — owner override of the ≤3 cap, plan 0013); identical URL in 48h = 0 credits unless the owner pulls fresh | `test_scraper_client.py::test_acceptance_3_*`, `test_scan_rotation.py::test_acceptance_3_*` |
| 4 | credit-balance never from get_stats | `test_scraper_client.py::test_acceptance_4_*`, `test_usage_alerts.py::test_get_stats_*` |
| 5 | midrank velocities match score-ideas.py | `test_scoring.py::test_acceptance_5_*` + golden test against the real file |
| 6 | no producibility term | `test_scoring.py::test_acceptance_6_*` |
| 7 | Call 3 alternates per table; no FB URLs → no FB calls | `test_scan_rotation.py::test_acceptance_7_*`, `test_missing_facebook_*` |
| 8 | remaining=15 → 2 calls; 0 → 0 | `test_scan_rotation.py::test_acceptance_8_*`, `test_zero_credits_*` |
| 9 | 429 → exactly one fallback; warning + resets_at | `test_gemini_ladder.py::test_acceptance_9_*` |
| 10 | unknown post_id → brief not written | `test_synthesis_brief.py::test_acceptance_10_*` |
| 11 | Like at remaining=15 → no transcript call | `test_like_script.py::test_acceptance_11_*` |
| 12 | old draft deleted, saved kept | `test_like_script.py::test_acceptance_12_*` |
| 13 | overlord helpers import no scrapers/gemini | `test_usage_alerts.py::test_acceptance_13_*` (AST check) |
| 14 | same `start_marketing_radar()` on any OS | `test_scheduler.py::test_acceptance_14_*` (macOS run; Windows untested) |

## 6. Decisions the spec left open (and what we chose)

- **Firestore layout:** `users/{uid}/marketingRadar/{meta,latest,contextCache,usage}` docs; lifecycle collections (`scans`, `posts`, `scripts`, `scrapeCache`, `playbook`, `notifications`, `chat`) under `meta`; `usage/events` and `usage/geminiDaily` under `usage`. Encoded only in `db/paths.py`.
- **Context path:** the spec default `users/{uid}/context` is a *collection* in Firestore terms (lesson 0001). `FirestoreBackend.get()` reads its first doc; override with `CONTEXT_PATH`.
- **Gemini SDK:** `google-genai` (lazy import). Ladder ids/caps from spec §12.1, overridable by `GEMINI_LADDER_JSON`.
- **Synthesis failure policy:** malformed JSON → one repair on a lower rung → degraded brief without `weekly_take` (`note="synthesis_malformed…"`). Unknown `post_id` → one repair → brief **not** written, posts persisted, alert `synthesis_warning`, `scan_index` not advanced.
- **Gemini exhausted during a scan:** brief is still written with ranked lists, `note="gemini_exhausted until …"`.
- **Chat:** JSON action protocol (not native function calling) so it works on every transport; max 2 tool rounds; **no scrape tool exists** so chat can never spend a credit.
- **Routes:** `services/api.py::RadarApi` returns `(status, body)`; 404 unknown ids, 400 bad input, 503 Gemini exhausted or backend down. `fastapi_router()` is optional; FastAPI is not a dependency.
- **Scan bookkeeping:** `scan_index`, `last_paid_scan_at/_id`, `next_scan_at`, `spent_last_scan` live on `latest`.
- **ScrapeCreators credits figure:** `meta.sc_credits_remaining`, updated from every paid response body/headers (multi-key, defensive).

## 7. Unverified assumptions — check these before trusting live output

1. **ScrapeCreators response shapes.** Every fixture in `tests/fixtures/scrapecreators/` and every key lookup in `scoring/normalize.py` is a best-effort guess (TikTok `aweme_list`/`statistics.digg_count`, Instagram `reels`/`like_count`, etc.). Also where `credits_remaining` / `credits_charged` actually appear (body vs header). **Action:** run one live `tiktok_trending` call, diff against the fixture, fix `normalize.py`, record a lesson.
2. **Parent context location.** See lesson 0001. Ask the dashboard owner for the real doc path.
3. **Gemini model ids on the free tier.** The ladder tries `gemini-3.8-flash → gemini-3-flash → gemini-3.5-flash` first and marks 404s as unavailable for the day; confirm current ids/caps at aistudio.google.com/rate-limit.
4. **`google-genai` error mapping.** `GenAiTransport` maps `APIError.code` 429 → `RateLimited`, 403/404 → `ModelUnavailable`; verify attribute names against the pinned SDK.
5. **Windows.** `pathlib`/`platformdirs` only, no OS-specific code, but nothing has been executed on Windows.
6. **Reddit/pytrends** are unauthenticated public endpoints and rate-limit unpredictably; both are wrapped so failures only flip `free_sources[].status` to `unknown`.

## 8. Not built (by design, spec §19) / out of scope

Standalone app or server, questionnaire UI, Marketing-section HTML/React (parent repo doesn't exist yet — `RadarApi` + `OVERLORD.md` are the contract), Facebook Graph / Ad Library, comments/demographics/pagination, Anthropic, Supabase, any write outside `marketingRadar/`.

## 9. Suggested review focus

- `db/firebase.py` + `db/paths.py` — is the namespace guard airtight for the real backend (reads of `context` only, writes only under the prefix)?
- `scrapers/client.py` — credit accounting when the API omits `credits_remaining`; the 402 path; the balance-endpoint gate.
- `jobs/scan.py::_synthesize_and_write` — the rejection/degrade policy above; `latest` pointer semantics for recap vs paid.
- `agent/gemini.py` — Pacific-day rollover, `min_rung` repair path, no retry loop on the same model.
- `agent/chat.py` — tool loop bounds; whether grounding payloads are small enough for the free-tier context.
- Security rules in spec §6.2 still need to be deployed by whoever owns the Firebase project.

## 10. Next steps for the receiving agent

1. Get keys → live `tiktok_trending` call → fix normalizers (assumption 1) → live end-to-end scan (spends 3 credits + 1 Gemini call).
2. Confirm `CONTEXT_PATH` with the dashboard owner; deploy §6.2 rules.
3. Build the parent Marketing section against `RadarApi` (empty state, board, Like → angles → script → save, saved library, chat, header pills from `get_stats()`, banners from `alerts[]` saying reset vs never-reset).
4. Wire `start_marketing_radar(user_id)` into the parent's startup and the overlord's prompt per `OVERLORD.md`.
5. Keep the process: new work → `plans/000N-*.md` first; any bug → `lessons/000N-*.md`.

---

# Part 2 — Voice AI Receptionist

**Spec (source of truth):** [plans/voice-receptionist/vr_plan.md](plans/voice-receptionist/vr_plan.md) — a build contract with its own rules (R0–R7) and locked decisions (V1–V12). **Plan + outcome:** [plans/0004-voice-receptionist.md](plans/0004-voice-receptionist.md). **Lessons:** [lessons/0003-mulaw-encoder-bias-is-14-bit.md](lessons/0003-mulaw-encoder-bias-is-14-bit.md), [lessons/0004-barge-in-must-cancel-tts-in-flight.md](lessons/0004-barge-in-must-cancel-tts-in-flight.md). **Docs the spec asked for:** [docs/latency-budget.md](docs/latency-budget.md), [docs/known-limits.md](docs/known-limits.md), [docs/setup-checklist.md](docs/setup-checklist.md).

## 1. What was built, by phase (vr_plan.md §13)

| Phase | Built | pytest gate | Real-call gate |
| --- | --- | --- | --- |
| 1 Twilio plumbing | `api/voice/{incoming,ws,status,fallback}.py`, `api/index.py`, `services/voice/{twiml,signature,audio,stream}.py`, `scripts/{render_greeting.py,warm.sh,tunnel.sh}`, `vercel.json` (`syd1`, 300 s) | `test_signature.py`, `test_audio.py` (cross-checked against `audioop` for all 65k int16 values), `test_twilio_stream.py` | deferred — needs a Twilio number + tunnel |
| 2 Simulator | `api/sim/app.py` + `sim.html` (Mode A text harness, Mode B mic → 8 kHz μ-law → **production** `/api/voice/ws`), `services/voice/sinks.py`, `telemetry.py` (HUD bus), `scripts/replay.py` | `test_sim_isolation.py` (LocalJsonlSink has no Firestore client; sim refuses to mount with a Firestore sink; `ws.py` has zero simulator branches by AST), `test_sim.py` (JS μ-law encoder + envelopes byte-identical to Python, checked with node) | — |
| 3 Routing + grounded answering | `services/voice/{router,answerer,tools,engine,templates}.py`, `config/fact_synonyms.yaml` | the ten `tests/conversations/*.yaml` suites through Mode A (`test_grounding.py`), `test_router.py` | — |
| 4 Booking + email | `services/voice/{booking_machine,email_capture}.py`, `services/booking/{capacity,commit,calendar,email}.py`, `scripts/seed_business.py` | `test_booking_machine.py`, `test_email_capture.py`, `test_booking_concurrency.py` (20 threads × 50 iterations, lock-based and simulated-optimistic) | deferred — real Firestore transaction, Calendar, email |
| 5 Resume, telemetry, hardening | `services/voice/{call_pipeline,providers}.py`, cutover timers, status-callback outcome, rollups, `config/pricing.yaml` | `test_call_pipeline.py` (audio turn end-to-end with fake VAD/STT/TTS, barge-in, cutover → resume, resumeCount > 3, status callback) | deferred — a call held past 280 s |

Run everything: `uv run pytest`. Run the simulator: `ENABLE_SIM=1 SESSION_SINK=local uv run uvicorn api.index:app --port 8000` → `http://localhost:8000/sim`.

## 2. Architecture in one paragraph

Twilio POSTs `/api/voice/incoming` (signature validated against `PUBLIC_BASE_URL`, never `request.url`), gets `<Connect><Stream>` with a 60 s HMAC token, and opens `/api/voice/ws`. `api/voice/ws.py` verifies the token (replay-safe), builds a `CallSession`, and hands every envelope to a `MediaHandler` from the pipeline factory — `ReceptionistPipeline` in production (VAD → end-of-turn → STT → `ReceptionistTurnEngine` → TTS with `clear` on barge-in and a server-side close at 280 s), or `EchoPipeline` when no audio providers are configured. `services/voice/engine.py` is the one per-turn brain: Groq router (one schema-constrained call, repair once, then CALLBACK) → deterministic booking machine (templates only, never Gemini) or `tools.answer_question` (confirmed Firestore data only) → Gemini phrasing with a template fallback on timeout, `CROSS_CONTACT` appended by Python. The simulator's `POST /sim/text` calls that same engine, so Mode A tests the production path. All writes go through a `CallSink`; the simulator's `LocalJsonlSink` cannot reach Firestore by construction.

## 3. What is deliberately NOT decided (R0) — every `TODO(spec)` in the tree

`grep -rn "TODO(spec)"` is authoritative; at handoff time:

- `config/capacity.yaml` — service windows, slot length, seats per window (empty; `seed_business.py` refuses to run).
- Business data — menu items, facts, allergen maps, `BUSINESS_ID`. Owned by the dashboard team. `tests/fixtures/business/` is test-only.
- `config/pricing.yaml` — every rate is 0; `costCents` is recorded as 0 with units kept.
- `config/fact_synonyms.yaml` — the question → lookup mapping §10 leaves to "Python".
- `services/voice/templates.py` — every utterance the spec describes but does not word (fallback apology, re-asks, callback lines, chit-chat fallback, resume-limit goodbye).
- `services/voice/booking_machine.py` — default meridiem for a bare "seven"; what to change when the caller says "no" at confirm.
- `services/voice/session.py` — who writes `historySummary` (Python does, no model).
- `services/voice/tools.py` / `scripts/render_greeting.py` — the runtime source of `{business_name}` (reads `facts/business_name` if present).
- `services/voice/providers.py` — where the Silero / Smart Turn ONNX files come from; Smart Turn's exact input features; the Scribe v2 *realtime* websocket contract (STT is one request per turn today).
- `services/booking/{email,calendar}.py` — email wording; where the daily Calendar retry cron runs.
- `api/sim/sim.html` — what "telephony fidelity OFF" should transmit (the socket only accepts 8 kHz μ-law).

## 4. Deviations from the letter of the spec (each flagged in code)

1. A fifth `<Parameter name="from">` on the `<Stream>` (§6.1 lists four) so the booking machine can offer caller ID.
2. Hand-rolled asyncio media loop on onnxruntime instead of Pipecat (user decision; §5 named Pipecat).
3. `api/index.py` as the single ASGI entry (Vercel treats each `api/*.py` as its own function); `vercel.json` rewrites `/api/*` to it.
4. Extra modules beyond §5: `engine.py`, `call_pipeline.py`, `providers.py`, `services/booking/*`, listed in plan 0004.
5. Phonetic confirmation of confusable letters is one combined question after spelling, not letter-by-letter.
6. Two Firestore trees coexist: marketing `users/{uid}/marketingRadar/**` vs voice `businesses/{businessId}/**`. Not reconciled — R2 forbids touching the marketing tree and R0 forbids inventing a mapping.

## 5. Unverified against live services

- Twilio: the exact `start`/`media`/`mark` field names follow the docs quoted in the spec; the signature-validation URL reconstruction is tested but not against a real tunnel.
- ElevenLabs: SDK method names (`text_to_speech.stream` / `convert_as_stream`, `speech_to_text.convert`) resolved defensively at runtime.
- Gemini: model ids in `config/thresholds.yaml` are intersected with `ListModels()` at boot; none of the preferred ids were checked against a key.
- Groq: JSON mode via `response_format={"type": "json_object"}`.
- Firestore: `FirestoreCommitter` is the §8.3 transaction shape; only the shared `decide()` arithmetic is exercised offline.
- Google Calendar: event id = idempotency key (hex ⊂ base32hex); 409 → update.

## 6. Suggested review focus

- `api/voice/ws.py` + `services/voice/signature.py` — token TTL/replay, close semantics after a server-side cutover.
- `services/voice/call_pipeline.py` — barge-in guard, the turn-task/TTS-task lifecycle, `finalize()` idempotency, what `_outcome()` writes vs what the status callback overrides.
- `services/booking/commit.py` — confirm the transaction reads the slot doc and never a query; run `test_booking_concurrency.py` against the emulator.
- `services/voice/booking_machine.py` — parser edge cases (dates past year end, "next Friday" semantics), the email phases state machine.
- `services/voice/tools.py::answer_question` — the mapping is heuristic; decide whether that is acceptable or should become a second Groq call.

## 7. Next steps for the receiving agent

1. Get keys → `scripts/tunnel.sh` → real call echo (Phase 1 gate) → render the greeting → `models/*.onnx` → real call with intelligence.
2. Get the business dataset and `capacity.yaml` from the dashboard team → `scripts/seed_business.py` → deploy Firestore rules/indexes.
3. Fill `config/pricing.yaml`, the `TODO(spec)` wordings in `templates.py`, and the email bodies with the owner.
4. Replace per-turn STT with the Scribe v2 realtime stream if partial transcripts matter for the demo HUD.
5. Keep the process: `plans/000N-*.md` before work, `lessons/000N-*.md` after any bug.

---

# Part 3 — Dashboard / Overlord (the web app)

**Plan + decisions:** [plans/0005-dashboard-wiring.md](plans/0005-dashboard-wiring.md). **Metric contract:** `UI/UI mockups project scope/uploads/metrics.md`. **Lesson:** [lessons/0005-gemini-thinking-models-spend-the-output-budget.md](lessons/0005-gemini-thinking-models-spend-the-output-budget.md).

## 1. What this is

`UI/UI mockups project scope/Uncle Tony Overlord Dashboard.dc.html` is a Claude Design export (one template + the vendored `support.js` runtime) whose data was hard-coded in its script block. It is now the product's front end, byte-identical: `dashboard/ui.py` serves it at `/`, rewrites the few numbers that were literal in the markup into template bindings, and appends `dashboard/bridge.js` inside the `<script data-dc-script>` block. The bridge runs after the design's `Component` class in the same scope (that is how `support.js` evaluates the block), loads `/api/dashboard/bootstrap` on mount, overrides the values `renderVals()` used to hard-code, and routes Like / Save / chat / Mark done / Approve / Dismiss / Overlord questions / period selector / notification toggles to the API.

## 2. Package map

```
dashboard/
  settings.py   config/dashboard.yaml → DashboardSettings (tz, opening hours, bands, assumptions)
  records.py    CallRecord/TurnRecord/BookingRecord/CallbackRecord/ReviewRecord; LocalJsonlSource (.testruns) and FirestoreSource
  analytics.py  metrics.md P0 formulas, pure; grading rules in the docstring
  present.py    entities → the template's shapes (callsData rows, tiles, KPIs, menu/packet/profile)
  marketing.py  MarketingHub: ScanDeps (offline or Firestore), questionnaire seed, trend cards, Like/Save orchestration
  overlord.py   grounded answer packet + Gemini transport (1024-token budget) + deterministic fallback
  routes.py     /api/dashboard/*, /api/overlord/ask, /api/jobs/*
  ui.py         page assembly (PATCHES table + bridge injection) and static mounts
  bridge.js     the client bridge
  app.py        mount_dashboard(app, config, deps) — called from api/index.py::create_app
data/business/uncle_tony/   menu_items.json, facts.json, marketing_context.json (from the mockup; owner-unverified)
scripts/seed_demo_calls.py  a week of calls through the REAL engine with scripted router output
scripts/seed_business.py    capacitySlots + menu + facts + questionnaire → Firestore
```

## 3. Decisions taken here (not in either spec)

- **Tenancy:** `MARKETING_USER_ID` defaults to `BUSINESS_ID`. The questionnaire is seeded from `data/business/<dataset>/marketing_context.json` when `users/{uid}/context` is empty (offline and Firestore; lesson 0001 path shape).
- **Labels:** route = BOOK → Booking, CALLBACK → Callback, ANSWER_QUESTION → Question, else Chitchat. Outcome = Booked / Couldn't answer (any `not_found` turn) / Callback logged / Answered / Abandoned / Error. Task success: booked or answered-without-gap = success; gap or error = failure; callback/abandoned = unknown (excluded). Coherence = "No data" until a grader exists. Latency sample = agent-turn router+answer ms ("not caller-perceived"). Handoff classes in `config/dashboard.yaml`; unknown reasons stay visible as Unclassified.
- **Marketing ↔ UI:** score is rank-normalised inside each list (top = 99); Like = `like_post` (angles + breakdown as the filming guide); Save = `choose_angle(post, 0)` + `save_script`; chat thread `ui`.
- **Zero-key mode:** `SESSION_SINK=local` relaxes the required env (`services/common/config.py::local_config`), the engine answers from `JsonBusinessReader(data/business/…)`, the marketing agent runs on fixtures, the overlord answers from templates. The audio pipeline loads only when ElevenLabs is configured.
- **Demo data label:** any `CAsim…` call flips the Analytics banner to "Demo data" (metrics.md asks for it).

## 4. Unverified

1. Vercel: `includeFiles` for the Python runtime and filesystem-vs-rewrite order for `/` have not been exercised on a real deployment; cron requests arrive as GET with `Authorization: Bearer $CRON_SECRET` (handled, untested live).
2. Firestore reads use single-field queries only; turns are read per call (≤400 calls per snapshot, cached 15 s). A busy month may need `collection_group("turns")` + an index.
3. The Uncle Tony prices/allergens/hours/40 seats are transcribed from the mockup — confirm with the owner.
4. The design runtime loads React/Babel from unpkg at page load; the page needs internet even when the backend is offline.

## 5. Next steps

1. Deploy to Vercel with the `.env.example` variables, run `scripts/seed_business.py`, hit `/api/jobs/marketing-scan` once, open `/`.
2. Real calls (Part 2 gates) then watch them land on the Voice AI calls screen; delete the `CAsim…` demo calls (or keep them and say so).
3. Fill `config/pricing.yaml` so the Cost column and `ai_cost_per_minute_cents` in `config/dashboard.yaml` stop reading 0 / "No data".
