# 0010 — Owner feedback round: chat gating, save, ladder cooldown, UI polish

**Status:** Done
**Date:** 2026-09-14

## Goal
One pass over a batch of owner feedback from a live demo session. Grouped by cause rather than by
report, because several symptoms shared a root.

## The Gemini ladder was the root of three reports
"Marketing agent did not switch models and instead just did not reply", "the save did not work, it
deleted the saved item", and the chat's "AI assistant is paused until midnight Pacific" were all the
same failure.

`GeminiLadder.generate()` treated *every* 429 as "today's free quota is gone" and wrote the model
into `daily.exhausted` until midnight Pacific. Gemini's free tier also returns 429 for its
**per-minute** rate limit, so one burst (a scan plus a couple of chat turns) walked the whole ladder
and retired all five free rungs in seconds. Nothing was left to step down to — which is why it "did
not switch models", and why Save, which needs Gemini to write a script, failed.

- `RateLimited` now carries `daily` and `retry_after`. `GenAiTransport` sets `daily=False` only when
  it can positively identify a short-window quota (`perminute` / `persecond` / `perhour` / `rpm` in
  the 429 body); anything unrecognised is still treated as the day's quota, so spec §12.1 and its
  acceptance test are unchanged.
- A short-window 429 writes `GeminiDaily.cooldown_until[model]` instead. The ladder skips a cooling
  model and steps down exactly as before, but the model comes back on its own in ~a minute.
- `GeminiExhausted.resets_at` is now the soonest of the cooldowns and the Pacific reset, so the chat
  can say "try again in about a minute" instead of "paused until midnight".
- `parse_ladder_json` drops any model whose id contains `pro` or `ultra`. A configured ladder can
  never reach for a paid model, whatever the env says.

**Save no longer depends on the AI at all.** The bookmark is a new `TrendPacket.saved` flag written
first and on its own; the Like → angle → script work is best effort after it, and its failure returns
200 with a note rather than a 503. `trends()` reads `savedIds` from that flag as well as from saved
scripts. Previously a 503 made the bridge roll back its optimistic state, which is exactly what "it
deleted the saved item" looked like.

## Agent chat now answers for itself before the data is in
`__dataState()` classifies what the agents can honestly answer from, and `sendChat` / `askOverlord`
reply locally instead of calling an API that will fail:

- bootstrap not back → "Your data hasn't loaded yet"
- a forced refresh in flight → "I'm still analysing the latest scan"
- no scan → "No trend data has loaded yet", plus the payload's own note
- a brief with no cards → "The scan has landed but I'm still analysing it"

The opening line was also seeded once and never revisited, so a panel first rendered before the scan
kept saying "No scan has run yet" long after the cards appeared. It is now refreshed when the state
changes, as long as the owner has not already written in the thread.

## Only YouTube Shorts in the scan
Not a bug: TikTok, Instagram and Facebook are ScrapeCreators endpoints and cost credits, while
YouTube, Reddit and Google Trends are the free sources. With no credits the scan still runs, on the
free sources only. `trends()` now returns a `platformsNote` saying which platforms are missing and
how many credits remain, and the bridge shows it beside the trend count instead of leaving it to look
broken.

## UI
- Every `.seg` segmented control (call filters, Trending for you/globally, period, analytics tabs)
  gets the solid-green treatment, not just the analytics one. `.seg-dark` — the 7D/30D switch on the
  dark chart card — keeps its own look. The label-sniffing tagger this used to need is gone.
- Overview quick-action tag pills follow the theme: amber for the one wanting attention, accent green
  for the rest.
- The sidebar is sticky at full height with the nav list scrolling inside it, so the Collapse button
  stays in view.
- My saves / My likes rows open that trend's script card (`openTrend`, which shows the card without
  also liking it — `likeTrend` and it now share one `__fetchAngles`).
- Business Context gets a back button to Profile; it is only reachable from there.
- Removed the profile Edit button (no edit flow behind it) and the Notifications card (the toggles
  persisted a preference nothing acts on).

## Follow-up 2: the pause was still sticking
The first pass classified an unrecognised 429 as the day's quota, "to be safe". In practice Gemini's
429s mostly name no quota at all, so that safe default *was* the bug: one burst still retired every
free rung until midnight Pacific, and nothing re-checked. Two changes:

- The transport now marks a 429 daily only when the body actually names a per-day quota
  (`perday` / `dailylimit`). Everything else is a short wait. Guessing "short" and being wrong costs
  a minute; guessing "daily" and being wrong costs the rest of the day.
- Daily exhaustion writes a cooldown too (to the Pacific reset), so every block has a recorded reason
  with an expiry. A model sitting in `exhausted` with **no** cooldown beside it — the state the
  previous build wrote, and what a running instance already has on disk — gets one probe instead of
  being inherited as dead. That is what un-pauses an agent that is already stuck.

`rung_statuses` reads the same cooldowns, so the usage snapshot and its alerts agree with what
`generate()` will actually do.

## Follow-up: the cooldown is a thinking state, not an answer
Telling the owner "try again in about a minute" made a transient rate limit look like a failure. A
cooldown now keeps the turn open instead:

- `ChatReply.retry_after` is set (and its holding text deliberately **not** persisted to the thread)
  when every free rung is merely cooling. `send(..., retry=True)` skips re-persisting the question,
  so an automatic retry is the same turn rather than a second one.
- The bridge holds the pending bubble, and re-asks up to `MAX_CHAT_RETRIES` (4) times as each
  cooldown expires. Only when those run out does it write a real answer into the thread.
- While a turn is in flight — any turn, not just a cooling one — the bubble becomes the Uncle Tony
  mascot beside a small italic line that cycles through `THINKING_WORDS` ("Pondering", "Mulling it
  over", "Chewing on it", …) every 2.4s, with a CSS-animated ellipsis. The mascot bobs.
- Both chat threads' rows were patched to carry the mascot `<img>` and a `.dc-dots` span, with the
  bridge deciding per row whether they show. `prefers-reduced-motion` stops the bob and the cycling
  ellipsis (a static "…" remains); the word still changes, since that is content, not motion.

## Outcome
24 patch anchors each occur exactly once, every binding they add has a default, `node --check` and the
stylesheet brace check pass. New tests: the per-minute cooldown and its shorter `resets_at`, a save
that sticks with the whole ladder spent, and a cooled-off chat turn that stays open without polluting
the thread. **Still not run** — no venv/`uv`/`fastapi` here (plan
0006). The ladder change touches spec acceptance test 9's area; it was written specifically to leave
that test's bare-429 behaviour intact, but that needs `uv run pytest` to confirm.
