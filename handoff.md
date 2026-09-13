# Handoff — Marketing Radar (marketing analysis agent)

**For:** a senior software engineer reviewing this work, and the coding agent they will point at it.
**State on 2026-09-13:** spec §16 steps 1–14 implemented offline-first, 117 tests green, nothing committed yet, no live credentials have ever been used.

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

A Python 3.11 **library** (`marketing_radar/`) the parent dashboard imports. Once a day it reads the parent's questionnaire from Firestore, every 2 days it scrapes TikTok + Instagram + one alternating secondary source via ScrapeCreators (≤3 credits, 100-credit lifetime budget, 15-credit reserve), always adds free YouTube/Reddit/Trends, scores with the AIOS formula, makes **one** Gemini free-tier call to write a `ScanBrief`, and exposes Like → 3 angles → script, a grounded chat, usage snapshot + alerts, and read-only overlord helpers. All writes land under `users/{uid}/marketingRadar/**`; nothing else in Firebase is touched. No Anthropic key anywhere.

## 3. How to run

```bash
uv sync --extra dev
uv run pytest                                   # 117 tests, ~1s, no network

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
| 3 | ≤3 live calls; identical URL in 48h = 0 credits | `test_scraper_client.py::test_acceptance_3_*`, `test_scan_rotation.py::test_acceptance_3_*` |
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
