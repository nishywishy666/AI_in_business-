# 0011 — Navigation motion, a working search, and a bell worth pressing

**Status:** Done
**Date:** 2026-09-14

## Goal
Make moving around the app feel deliberate, and give the two decorative header controls a job.

## Changing screens
- **The sidebar highlight now travels.** Previously each nav item painted its own cream background,
  so switching meant one blinking off and another on. The highlight is now a single pseudo-element of
  the nav list whose box comes from custom properties (`--dc-nav-top/left/w/h`); `__placeNavPill()`
  measures the active item after every render — via a wrapped `componentDidUpdate`, plus mount and
  resize — and the pill slides between them on a slightly overshooting curve. The items themselves
  are forced transparent so only the pill paints. Screens with no nav item (Profile, Business
  Context) fade it out rather than stranding it.
  - The active item is found by reading `el.style.background`: the export gives the nav items no
    class, and only the active one is painted, so its own inline style is the marker.
  - `dc-nav-ready` is added a frame after the first measurement, so the pill lands silently on load
    instead of sliding in from the top of the list.
- **The screen itself rises in.** `setScreen` is wrapped to replay a short fade-and-lift on the main
  `.scrollpane` (with a forced reflow, so the same animation runs on every change, not just the
  first) and to reset its scroll to the top.

## Header search
The box was decorative. It now searches `SEARCH_INDEX` — the app's own screens plus four actions (My
saves, My likes, Refresh trends, Ask the Overlord) — and picking a result navigates there. Matching
ranks label-prefix over label-contains over a `keys` field holding the words people actually type for
a place that isn't called that ("transcript" → Voice AI calls, "billing" → Profile). Arrow keys move
the selection, Enter opens, Escape clears, clicking outside closes. Results unroll with a 32ms
per-row stagger inside the popup animation the floating cards already had.

## Header bell
Also inert. `present.notifications()` now builds rows from payloads the bootstrap already computes —
no extra reads — ordered by what is actually waiting on Tony:

| row | when | goes to |
| --- | --- | --- |
| *N callbacks waiting* | any open callback | Callbacks |
| *N questions to review* | unreviewed gaps | Callbacks |
| *Trend scan … is in* | a scan with cards | Marketing |
| *Some platforms sat out this scan* | `platformsNote` set | Marketing |
| *Call data is stale* | `freshness.stale` | Overview |
| *All caught up* | nothing else | Overview |

The bell carries an amber badge counting the urgent/warning rows, each row has a tone dot, and
clicking one closes the panel and navigates.

## Marketing: the scan's numbers get their own strip
The count line had grown into a run-on sentence ("Showing 5 of 5 · scan 2026-09-13 · No Instagram,
Facebook posts in this scan — 94 ScrapeCreators credits left."). It is back to just what is shown,
and the scan's own numbers — trends, credits left, credits spent, saved, next scan — are a card of
labelled stats directly above the agent chat, with **Refresh now** pinned to its right edge
(`margin-left:auto`). The platform note sits under a divider inside that card.

## Why TikTok and Instagram had no data
Every scan row already calls TikTok and Instagram first — they were being scraped. The posts were
being thrown away in `_items()`: it looked for the list under a fixed set of top-level keys, and
CLAUDE.md flags that the live ScrapeCreators envelopes were never recorded, so any wrapper key we did
not guess normalised to zero posts. YouTube came through because it is the YouTube Data API, whose
shape *is* known.

`_items()` now falls back to `_deep_items()`, which walks the payload (to depth 4) and takes the
largest list of dicts that look like posts. A scan call that is answered but yields nothing also logs
a warning naming the endpoint, so the next live run says which one is still unreadable. Verified in
isolation against a made-up `payload.collection.nodes` envelope, a GraphQL `edges` one, the Reddit
listing shape, the known keys, and an error body with no posts at all.

## Also
- Segmented controls get the same rising-circle hover as the buttons, on the options you can still
  pick — the selected one keeps its ring rather than being covered by the circle.
- The collapsed sidebar's mascot goes 36px → 52px; it is the only branding left at that width.
- The platform filter icons go 18px → 22px and lift slightly on hover.

## Typography: Outfit only, on three weights
856 for titles and numbers, 577 for everything else, 267 for captions and muted lines. Outfit is
already loaded as a variable font (`wght@100..900`), which is what makes non-multiple-of-100 weights
land exactly; the font link drops Archivo, which was the heading face and is no longer used.

The export hard-codes `font-weight` inline on its numbers and status words (400/500/600/800), and
inline styles beat a stylesheet, so the four are mapped onto the scale by matching the style
attribute itself (`[style*='font-weight: 500']` → 856, and so on), in a rule placed last so an
explicit design weight wins. Muted colour is this design's marker for a secondary line, so
`[style*='color: var(--color-neutral-500)']` and its 400 sibling carry the 267. Both spaced and
unspaced spellings are matched, since that depends on how the browser serialises the attribute.

Buttons, tags, segment labels and table headers are **577**, not 856 — they are UI chrome rather than
titles or numbers. Easy to flip if they read too light.

## Outcome
30 patch anchors each occur exactly once, every binding has a default, `node --check` and the
stylesheet brace check pass. A test asserts every notification row names a real screen. **Not run and
not seen in a browser** — no venv/`uv`/`fastapi` here (plan 0006). The sliding pill is the piece most
worth watching: it depends on measuring the active item after React commits, and on the nav items
keeping the inline background the export gives them.

## MagicBento + ClickSpark (reactbits), ported rather than installed
Neither component can be dropped in: `UI/` is a pinned Claude Design export whose DOM belongs to
React, there is no bundler to import a `.jsx` into, and GSAP is not loadable (the app must run
offline). Every effect both components ship is reproducible without either, so they were rebuilt in
the bridge's stylesheet plus one controller — and in the theme's own green (`--dc-glow: 31,74,69`,
`--color-accent`) instead of the default purple.

**MagicBento**, on every tiled card (`.card.elev-sm`):

| effect | how |
| --- | --- |
| border glow | a masked radial-gradient `::after` ring, driven by `--dc-glow-x/y/intensity` |
| global spotlight | one fixed element following the cursor, faded by distance to the nearest card |
| tilt + magnetism | a single `perspective/rotateX/rotateY/translate3d` transform, CSS handles the easing |
| click ripple | one appended element on a keyframe, removed on `animationend` |

The star particles were built and then removed at the owner's call: drifting dots over a KPI card
read as dirt on the screen rather than sparkle.

One rAF-throttled `mousemove` drives all of it, and it measures every card's rect **before** writing
any of them — interleaving reads and writes forces a layout per card per frame. `textAutoHide` was
deliberately not ported: these cards carry real data, and line-clamping would hide it.

Two opt-outs: the marketing chat and stats panels (tilting a card you are typing into is not a
feature), and any card painting its own inline background — which is how the dark Call volume card is
recognised, whose hover tooltip is drawn outside its own box and would be clipped by the
`overflow:hidden` the particles and ripple need.

**ClickSpark** is one fixed canvas over the app, eight accent-green lines radiating from each click,
with the component's own ease-out curve. It draws only while sparks are alive rather than holding a
permanent rAF loop.

Both are off under `prefers-reduced-motion` and at ≤768px, matching the components' own
`disableAnimations` behaviour.

## Analytics: Custom opens a calendar
`period_for("custom", ...)` used to be a second name for "last 30 days". It now takes real dates:
both ends are the owner's local days, inclusive, the window never runs past now, a backwards
selection is swapped, and with no dates it still falls back to the old 30 days.
`GET /api/dashboard/analytics?period=custom&start=…&end=…` carries them, and the period label becomes
the range itself ("7–9 Sep").

The picker is a popover anchored to the period switcher: a Monday-first month grid built in the
bridge, two clicks for a range (first the start, second the end — backwards is accepted), today
ringed, future days dead, the selected span filled in accent. It arrives on the same pop animation
the other floating cards use, its cells unroll with the search list's stagger, and clicking outside
closes it. A chosen range is held separately from the 60-second bootstrap's cache so the poll cannot
overwrite it.

## Marketing: a toastie doing laps
While the trend scan is loading — the first bootstrap, or a forced refresh — the trend column shows a
ring with a toasted sandwich orbiting it rather than an empty space. The sandwich is drawn inline
(the export ships only the logo and the mascot): crust, cut face, a melted-cheese line, and two steam
curls.

Three cycles run at once: the arm rotates, the toastie counter-rotates by exactly the same period so
it stays upright as it travels, and a shorter wobble plus drifting steam ride on top. Under
`prefers-reduced-motion` only the ring turns, and slower.

## Both chats say what is answering them
A small line under each chat's title: the free model in use, how much of its daily allowance is
gone, and the ScrapeCreators credits behind the trend data — with a status dot (green answering,
amber every rung busy, grey still checking) and the model id plus any quality warning on hover.

Both chats are Gemini free tier, so one line serves both: the marketing agent goes through
`marketing_radar`'s ladder, and the Overlord through its own transport with its own resolved model.
`MarketingHub.ai_status()` reads the usage snapshot and is deliberately best effort — it returns the
empty shape rather than raising, because a chat that still works must not be blocked by the line that
describes it. It rides on the bootstrap, with `GET /api/dashboard/ai` for a direct read.

Clicking anywhere outside the Overlord closes it, through the same animated path as the profile menu:
the panel and the mascot that opens it share one fixed parent, so "outside" is anything not inside
that parent — which keeps the mascot's own click a plain toggle rather than a close-then-reopen race.
