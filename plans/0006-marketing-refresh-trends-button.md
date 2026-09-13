# 0006 — "Refresh now" button on the Marketing screen

**Status:** Done
**Date:** 2026-09-14

## Goal
Give the owner a manual way to re-read the trend scan from the Marketing screen, so an empty or
still-loading trend list is recoverable without reloading the page or waiting for the 60s poll.

## Context
The Marketing screen renders whatever `GET /api/dashboard/bootstrap` last returned. Three things
could leave it blank with no way out:

- `get_brief()` goes through `daily_pull()`, which serves the **local cache at most once a calendar
  day** — a scan that lands after the pull is invisible until the next day.
- `MarketingHub` memoises `ScanDeps` for the life of the process; if the first build fails (missing
  keys, Firestore unreachable) every later read fails the same way.
- `MarketingHub.trends()` called `self.deps()` outside its `try`, so a build failure turned the whole
  `/api/dashboard/bootstrap` call into a 500 — the dashboard showed "Loading…" forever, not just an
  empty marketing list.

## Approach
`UI/` stays byte-identical (plan 0005); the button is added through the `dashboard/ui.py` `PATCHES`
mechanism and wired in `dashboard/bridge.js`, like every other live binding.

- `marketing_radar/services/brief.py` — `get_brief(..., force=False)` forwards to `daily_pull(force=)`.
  Still a pure read: no scrape, no credit spent.
- `dashboard/marketing.py` — `MarketingHub.reset()` drops the memoised settings/deps (restoring any
  injected test doubles), `brief(force=)` / `trends(force=)` thread the flag through, and `trends()`
  now catches a deps-build failure and reports it in `note` instead of raising.
- `dashboard/routes.py` — `POST /api/dashboard/trends/refresh` returns the same trends payload, forced.
- `dashboard/ui.py` — one new patch turning the trend-count line into a flex row with the button.
- `dashboard/bridge.js` — `P.refreshTrends()` posts, then re-runs `__refresh()`; `refreshing` /
  `refreshError` drive the button label and the count line, bound on **both** render paths (the
  no-data path is the one the button exists for).
- `tests/test_dashboard_routes.py` — the endpoint survives the empty state and picks up a scan that
  landed after the daily pull. `tests/test_dashboard_ui.py`'s binding check now excludes every
  binding the export already carries (a patch may legitimately re-emit `{{ trendCountLabel }}`)
  rather than one hard-coded name.

## Outcome
Built as planned. Verified statically: all 14 patch anchors still occur exactly once and the button
renders in place, the bridge's DEFAULTS cover every binding the patches add, and `node --check`
passes on `bridge.js`. **pytest was not run — this machine has no project venv, no `uv`, and no
`fastapi` installed**, so `uv run pytest` still needs to be run once by someone with the environment.
