# Overlord integration (read-only)

The overlord (main dashboard agent) answers marketing questions by **reading** what the marketing agent already wrote. It never scrapes, never calls Gemini for a new brief, and never writes under `users/{uid}/marketingRadar/**`.

```python
from marketing_radar.services import get_marketing_summary, get_marketing_stats

summary = get_marketing_summary(store, cache, settings)   # dict | None  (None = no scan yet)
stats   = get_marketing_stats(store, cache, settings)     # spec §13 usage snapshot | None
```

`store` / `cache` / `settings` are the same objects the marketing package built (`marketing_radar.deps.build_deps(user_id, settings)` → `deps.store`, `deps.cache`, `deps.settings`).

`get_marketing_summary()` returns: `scan_id`, `generated_at`, `kind`, `next_scan_at`, `weekly_take`, `patterns`, `ignore`, `film_this` (post + why), top-3 `niche` and `global` cards with real metrics, `credits`, `saved_scripts` count, a compact `stats` line, `alerts` messages, and `source_note`.

Rules for the overlord's own prompt:
- Cite the `scan_id` in every marketing answer ("From scan 2026-09-12…").
- Say whether a limit **resets** (Gemini / YouTube: midnight Pacific) or **does not reset** (ScrapeCreators).
- If `summary` is `None`, say the first scan is scheduled; if `stats.stale` is true, say the numbers are older than a day.
- These helpers import only `services.brief` / `services.stats` — no scrapers, no Gemini (enforced by `tests/test_usage_alerts.py::test_acceptance_13_*`).

## The dashboard's Overlord packet (plan 0013)

`dashboard/routes.py::/api/overlord/ask` sends Gemini more than the marketing summary: `present.knowledge_payload()`
adds the business's own database — the confirmed menu with prices and allergens, every fact document, bookings,
callbacks **with phone numbers**, the last 30 calls with one-line summaries, and the unanswered questions — and
`present.knowledge_lookup()` reuses the receptionist's `answer_question()` so "what's on the menu" / "do we deliver"
are answered from the same confirmed data callers hear. The template fallback (`dashboard/overlord.py`) answers
those from the packet too, so menu and fact questions work with no model at all. Still read-only: nothing here
scrapes, and the marketing helpers above are unchanged.
