# AI_in_business-

Main repository for the **AI for Businesses Hackathon** — tracks the web-app build from initial setup through to demo.

## Structure
- [CLAUDE.md](CLAUDE.md) — working agreement for how this repo is developed (read this first)
- [handoff.md](handoff.md) — review guide for the marketing agent: where everything is, decisions, open assumptions
- [OVERLORD.md](OVERLORD.md) — how the main dashboard agent reads marketing data
- [plans/](plans/) — plan of changes, written before each non-trivial piece of work
- [lessons/](lessons/) — lessons learned from mistakes/bugs, written after each one
- [marketing_agent_plans/](marketing_agent_plans/) — product plan + technical spec for the marketing agent
- [marketing_radar/](marketing_radar/) — the marketing analysis agent library (Python 3.11)
- [tests/](tests/) — pytest suite + recorded fixtures (no network, no credits)
- [previous_work/](previous_work/) — earlier Editoz AIOS repo, reference only

## Marketing agent — quick start

```bash
uv sync --extra dev                      # Python 3.11 venv + deps
uv run pytest                            # 117 tests, spec §17 acceptance tests included

# Run the whole pipeline offline (fixtures + JSON-file Firestore stand-in, zero credits)
c() { uv run python -m marketing_radar.cli --offline --user-id demo "$@"; }
c scan                                   # paid scan → ScanBrief
c recap                                  # off-day recap
c brief; c stats                         # latest brief / usage snapshot + alerts
c like <post_id>; c angle <post_id> 1    # Like → 3 angles → draft script
c save <script_id>; c scripts            # save (never expires) / list
c chat "what should I post tomorrow?"    # marketing chat with tools
c overlord                               # read-only summary for the main agent
c expire; c reset                        # draft cleanup / clear offline state
```

Live mode: copy `.env.example` to `.env`, fill in the Firebase / ScrapeCreators / Gemini keys, and drop `--offline`. The parent dashboard wires the scheduler with `from marketing_radar.scheduler import start_marketing_radar`.

## Voice AI receptionist — quick start

Spec: [plans/voice-receptionist/vr_plan.md](plans/voice-receptionist/vr_plan.md). Code: `api/`, `services/voice/`, `services/booking/`, `config/`, `contracts/voice.py`.

```bash
uv sync --extra dev --extra voice
uv run pytest                                      # marketing + voice suites, no network

# Local simulator (writes only to ./.testruns, never to Firestore)
cp .env.example .env                               # fill what you have; dummy values are fine offline
ENABLE_SIM=1 SESSION_SINK=local uv run uvicorn api.index:app --port 8000
open http://localhost:8000/sim                     # Mode A: type a turn · Mode B: Call (mic → 8 kHz μ-law → production /api/voice/ws)
uv run python scripts/replay.py .testruns/<callId>.jsonl

# Real phone calls (needs the keys in .env.example)
scripts/tunnel.sh                                  # prints PUBLIC_BASE_URL + the wss:// URL for the Twilio console
uv run python scripts/render_greeting.py --business-name "Your Restaurant"
uv run python scripts/seed_business.py --dry-run   # capacitySlots from config/capacity.yaml (TODO(spec): fill it)
```

Before a demo: [docs/setup-checklist.md](docs/setup-checklist.md). Known limits: [docs/known-limits.md](docs/known-limits.md). Latency targets: [docs/latency-budget.md](docs/latency-budget.md).

Dashboard folders will be added when that track starts.
