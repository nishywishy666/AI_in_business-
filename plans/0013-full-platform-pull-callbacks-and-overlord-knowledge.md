# 0013 — Full four-platform pull, "Refresh now" pulls, callback detail, video links, Overlord knowledge

**Status:** Done
**Date:** 2026-09-14

## Goal
Five owner requests from a demo session, in one pass:
1. Callbacks: an **Open** button per card shows the caller's full phone number and the transcript of the
   call that produced it. Numbers are no longer masked anywhere on the Callbacks screen.
2. Every paid scan pulls **TikTok, Instagram, Facebook and YouTube Shorts** from ScrapeCreators, every
   2 days **and** whenever the owner clicks **Refresh now**. No platform is dropped to save credits.
3. Every trend card links to the video it came from.
4. The Overlord answers from the **whole business database** (menu, facts, bookings, callbacks, call log,
   unanswered questions) as well as the call analytics and the latest scan.
5. The "Demo data" tag in the top-right header goes away.

## Context
- Spec constraint 5 says "max 3 live paid calls per scan" and §10.3 rotates a *third* call between
  Facebook and YouTube Shorts, shedding it inside the 15-credit reserve. The owner has overridden that
  numbered constraint in chat ("pull data for instagram, tiktok, facebook and youtube shorts at once …
  do not avoid scraping from any socials"), so this plan changes it on purpose (CLAUDE.md: the spec
  wins *unless the user overrides a numbered constraint*).
- ScrapeCreators has **no keyword or trending endpoint for Facebook** (checked against
  docs.scrapecreators.com on 2026-09-14): organic Facebook content only comes from a page
  (`/v1/facebook/profile/reels`) or a group (`/v1/facebook/group/posts`), both URL-keyed. The Uncle Tony
  questionnaire has no Facebook URLs, so without a fallback Facebook would silently sit out every scan —
  exactly what the owner asked us not to do.
- "Refresh now" (plan 0006) was a *read*: it re-read the brief from Firestore without scraping. The owner
  wants it to be a pull.

## Approach
### Marketing agent (`marketing_radar/`)
- `jobs/scan.py::plan_calls` — one call per platform, four slots, every scan: TikTok (trending /
  hashtag / keyword rotating by `scan_index`), Instagram (hashtag / trending reels alternating), Facebook
  (page reels ↔ group posts when both exist, else whichever exists, else the fallback page), YouTube
  Shorts trending (always, whatever the free YouTube Data API did). Credit shedding only ever drops calls
  we literally cannot pay for: `None` → all four, `0` → none, `1..3` → the first N, `≥4` → all four. The
  reserve no longer removes a platform (owner override); it still gates Like-transcripts in `agent/like.py`.
- `run_paid_scan(deps, *, fresh=False)` and `ScrapeCreatorsClient.fetch(..., fresh=False)` — `fresh`
  skips the 48h request cache *read* (still writes it), so a manual pull is a real pull.
- `config.py` — `max_live_calls` 3 → 4, `next_scan_estimated_cost` 3 → 4, new
  `facebook_fallback_page_urls` (env `MARKETING_FACEBOOK_FALLBACK_URLS`, comma-separated; default: Tasty's
  public page, a large food-video page, so a food business without its own Facebook URLs still gets a
  Facebook column). Owner-unverified default; the questionnaire's own URLs always win.
- `cli.py scan --fresh`.

### Dashboard (`dashboard/`)
- `marketing.py::MarketingHub.pull_now()` — runs the paid scan with `fresh=True` under a non-blocking
  lock (a second click while one is running gets "already pulling" instead of a second spend), then
  returns `trends(force=True)` with a `pull` block (scan id, live calls, credits spent, note). `trends()`
  also carries `pullCost` / `pullPlatforms` so the button can say what it will spend. `_platforms_note`
  says *why* a platform is missing (credits, or Facebook without a URL).
- `routes.py` — `POST /api/dashboard/trends/refresh` calls `pull_now()`. `/api/overlord/ask` builds the
  packet with `present.knowledge_payload()` (menu, facts, bookings, callbacks with phone numbers, the
  last 30 calls with summaries, unanswered questions) and a `lookup` answered by the receptionist's own
  `answer_question()` for the question asked, so the template fallback can answer menu/fact questions too.
- `present.py` — `callback_row` returns the full number (`format_phone`: +61 → 0-prefixed, spaced) plus
  `phone`, `fullName`, and the linked call's transcript is looked up client-side from the bootstrap's
  `calls`. `mask_phone` removed.
- `overlord.py` — `build_packet(..., knowledge=)`, SYSTEM prompt told about the business section, and
  `template_answer` answers menu/fact questions from the lookup before the analytics branches (unless the
  question is a count question: "how many", "this week", …).
- `ui.py` patches — header badge gets a `style` binding (hidden unless a refresh failed); callback cards
  get **Open** and a dialog (name, number, question, reason, wait, status, transcript); trend cards'
  thumbnail box becomes a **Watch on <platform>** link (thumbnail as background when the packet has one)
  and a ▶ Watch button joins Like/Save; the refresh button's title is a binding.
- `bridge.js` — `openCallback`/`closeCallbackModal` (+ exit animation), `callbackModal` built from
  `d.callbacks` + `d.calls`; `onWatch` opens the post URL in a new tab; `refreshTrends` confirms the
  credit spend when online, shows "Pulling…", and surfaces the pull note in the stats strip;
  `headerBadgeStyle`; search-index copy.

### Tests
- `test_scan_rotation.py` rewritten for four slots (acceptance 3/7/8 renamed to the new policy, with a
  note pointing here); `test_scraper_client.py` gets a `fresh=True` case.
- `test_dashboard_routes.py`: refresh now scans (first refresh on an empty app returns a brief; a second
  one spends again), callbacks carry an unmasked `number`/`phone`, the Overlord answers a menu question and
  a fact question with no model.
- `test_dashboard_ui.py` keeps checking every new anchor/binding automatically.

## Outcome
Built as planned; `uv run pytest` is green (this machine now has the project venv, unlike plans 0006/0010).
Deviations and things worth knowing:
- Facebook without a questionnaire URL falls back to Tasty's public page (`MARKETING_FACEBOOK_FALLBACK_URLS`
  overrides; empty disables). A group URL in the questionnaire beats the fallback; page+group alternate.
- "Refresh now" spends 4 credits per click when online, so the bridge asks the owner to confirm first
  (no prompt in offline/demo mode). A second click while a pull runs is reported, not doubled.
- `config/fact_synonyms.yaml`: `parking` moved above `hours`/`address`, otherwise "where do people park"
  resolved to the address (the same first-hit-wins rule plan 0012 documents).
- The Overlord's template fallback answers menu and fact questions and names open callbacks with their
  numbers; record questions ("who is waiting on a callback") never fall into the fact lookup.
- Spec acceptance tests 3/7/8 were rewritten for the new policy rather than deleted; the "Demo data" text
  still appears in the Analytics footer's freshness line (`dataSourceLabel`), which was not asked about.
- Lesson 0007 gained a "recurred" note: scripted exact-match edits must be CRLF-neutral in this checkout.
