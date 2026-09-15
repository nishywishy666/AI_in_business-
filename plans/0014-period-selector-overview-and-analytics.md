# 0014 — Period selector on Overview and Analytics

**Status:** Done
**Date:** 2026-09-16

## Goal
Give the owner a Today / This week / This month selector on both the Overview (main dashboard) and the
Analytics screen, so every number on those screens is scoped to a window they chose.

## Context
The backend already computes any period: `analytics.period_for()` supports `today`, `week` (rolling 7
days), `30d` and `custom`, and `/api/dashboard/analytics?period=…` serves the whole presented payload
(`kpis`, `routeMix`, tiles, gaps) for one period. What was missing was front end:

- **Analytics** already had a selector, but only `Today / This week / Custom` — no month.
- **Overview** had no selector at all. Its KPI cards, route donut and calls table were pinned to today
  (`routes.DashboardContext.bootstrap` computes `raw_today` for `overview.kpis`). The only range control
  there, 7D/30D, drives the call-volume chart series alone (`daily_series`), not the tiles.

## Approach
1. **`dashboard/analytics.py`** — add a real `month` period: calendar month-to-date in the business
   timezone (since the 1st), label "this month", previous-period label "the period before". `30d` stays
   as it was for API callers. Deliberately calendar-based, not rolling-30, so the button means what it
   says; see the follow-up below about `week`.
2. **`dashboard/ui.py`** — two new patches on the Overview screen:
   - the "Last refreshed …" line becomes a flex row with a `seg` bound to `{{ overviewPeriodOptions }}`;
   - the hard-coded "Today's calls" card title becomes `{{ overviewCallsTitle }}`.
   Analytics needs no new patch — its `seg` already iterates `{{ periodOptions }}`.
3. **`dashboard/bridge.js`**
   - `periodOptions` is rebuilt as `Today / This week / This month / Custom` (the export only emitted
     three, and Custom must stay last because it is the calendar's own button).
   - new `setOverviewPeriod` + `overviewPeriod` state; the Overview's KPIs, route donut, calls table and
     table title read `live.analytics[overviewPeriod]` instead of the bootstrap's today-only payload.
   - one `__ensurePeriod(key)` helper fetches and caches a period payload, de-duped by a pending map;
     `setAnalyticsPeriod` now goes through it too.
   - `__apply` drops the per-period cache on every bootstrap and re-fetches only the two periods on
     screen. Without this a cached `week` payload was never refreshed again after the first fetch —
     harmless when only Analytics used it, wrong now that the Overview leans on the same cache.
4. **Tests** — `tests/test_dashboard_analytics.py` gets a month-period case; the UI patch/defaults tests
   already enforce that `overviewPeriodOptions` and `overviewCallsTitle` have bridge defaults.

## Outcome
Built as described; `uv run pytest` green. Two follow-ups for the owner:
- `week` is still *rolling 7 days* labelled "this week", while `month` is calendar month-to-date. Making
  `week` "since Monday" would make the control internally consistent, but changes existing numbers, so
  it was left alone.
- Overview quick actions and notifications are still today-scoped on purpose — they are a worklist
  ("what needs you now"), not a report.
