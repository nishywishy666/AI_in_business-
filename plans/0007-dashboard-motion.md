# 0007 — Motion for the dashboard's popups and collapses

**Status:** Done
**Date:** 2026-09-14

## Goal
Make the dashboard's popups and collapsing sections open and close smoothly instead of snapping:
the three dialogs, the two floating popups (profile menu, Overlord panel), the sidebar collapse, and
the expanded call-transcript row.

## Context
`UI/` is a pinned Claude Design export (plan 0005) and is never edited, so all of this lands in the
injected `dashboard/bridge.js` — the same place the analytics segmented-control restyle already
lives. Two constraints shape the approach:

- The page renders through React (`support.js` → `getReact().createElement`). A closed popup is
  unmounted on the next render, so a CSS exit animation has nothing left to play on.
- The popups are unclassed `div`s in the export; only their inline styles tell them apart. `support.js`
  converts each style string into React's style object (`cssToObj`), so they can be identified from
  `el.style` and tagged — the same MutationObserver trick the analytics switcher already uses.

## Approach
- **Entry — pure CSS.** `@keyframes` on `.dialog-backdrop` / `.dialog-backdrop > .dialog`, on
  `.card.elev-lg` (the only two elev-lg cards in the export are exactly the two floating popups, so
  they animate even before the observer tags them), and on `.table td[colspan]` for the expanded row.
- **Exit — JS wrappers.** `wrapClose()` at the bottom of the bridge wraps `closeSummaryModal`,
  `closeSavedLikedModal`, `closeScriptModal`, `saveScriptToLibrary`, `toggleOverlord`,
  `toggleProfileMenu` and `toggleCall`: tag the live node with `.dc-closing`, wait 170ms, then run the
  original. The two toggles and `toggleCall` only animate on the closing half of the toggle.
- **Exit keyframes are separate, not `animation-direction:reverse`.** By close time the entry
  animation has already finished; changing direction on a finished animation does not restart it and
  the element would jump straight to the end state. Only a change of `animation-name` restarts one.
- **Sidebar:** the export sets `transition:width .15s` inline; `.dc-sidebar` overrides it with a
  longer eased curve (`!important`, since it is beating an inline declaration).
- **`prefers-reduced-motion: reduce`** disables every animation and transition, and the JS skips the
  170ms hold so closing stays instant.

### Known limitation
The transcript row is a `<td>`, and a table cell cannot animate its own height. The reveal is its
padding opening up under a fading, sliding body — it reads as a collapse, but the row's height still
lands in one step. A true height animation would need a wrapper element inside the cell, which means
editing the pinned export.

## Outcome
Built as planned; nothing under `UI/` changed, so the export hashes still hold. Verified `node --check`
on `bridge.js` and that the generated stylesheet is brace-balanced with the intended specificity
order (`.dc-closing` rules outrank their entry rules; the reduced-motion block outranks both).
Not verified in a browser — no environment here to run the app (see plan 0006).
