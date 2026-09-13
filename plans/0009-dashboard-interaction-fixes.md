# 0009 — Overlord chat box, working 7D/30D toggle, profile affordances

**Status:** Done
**Date:** 2026-09-14

## Goal
Four interaction gaps the mockup left behind, all reachable from the Overview and the sidebar:

1. The Overlord panel offers three canned questions and no way to type anything else.
2. The Call volume card's 7D/30D switch is decorative — 7D is `readOnly` and 30D has no binding.
3. The caret beside the business name reads as "sort", but it opens a profile menu.
4. That menu can only be closed by clicking its trigger again.

## Context
`UI/` is a pinned export, so every markup change goes through `dashboard/ui.py`'s `PATCHES` (each
anchor must occur exactly once; `tests/test_dashboard_ui.py` fails loudly if a re-export moves one)
and every behaviour through `dashboard/bridge.js`.

## Approach
**Overlord input.** Patch in the same input row the Marketing chat already uses, bound to
`overlordDraft` / `setOverlordDraft` / `overlordKeyDown` / `sendOverlord` in the bridge. `sendOverlord`
trims, clears the box and delegates to the bridge's existing `askOverlord()`, so it goes through
`POST /api/overlord/ask` with the same pending-bubble and error handling as the quick questions.

**7D/30D.** The design derives the entire chart — line path, area, grid, hover tooltip and the delta
percentage — from `this.callVolumeValues` / `callVolumeDates`. So the toggle only has to swap those
two arrays before `origRender` runs; everything downstream follows for free.

- `an.daily_series()` already took a `days` argument. `bootstrap()` now returns `overview.chart30`
  beside `overview.chart`, so the switch never waits on a request.
- At 30 points one x-label per point is unreadable, so `daily_series` blanks all but every fifth
  label (plus today) above 10 days, and the bridge thins the dashed grid lines to match.
- The card's footer said "vs first day in last 7 days" as literal text; it is now a binding that
  follows the range, and the hard-coded `↑` became a binding that follows the sign.

**Profile affordances.** The caret becomes a stroked person glyph in the same 16-box style as the
platform filter icons. Click-out is a capturing `mousedown` listener on `document`: the menu and its
trigger share one positioned parent, so "outside" is anything not inside that parent — which is what
keeps a click on the trigger a plain toggle rather than an open-then-immediately-close race with
React's own handler.

## Outcome
Built as planned. 18 patch anchors still occur exactly once, every binding the patches add has a
default in `bridge.js`, and `node --check` passes. New tests cover the 30-day series' shape and label
thinning, and the `chart30` payload. Still not run here — no venv/`uv`/`fastapi` on this machine (see
plan 0006).
