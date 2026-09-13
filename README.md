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

Dashboard and Voice AI receptionist folders will be added when those tracks start.
