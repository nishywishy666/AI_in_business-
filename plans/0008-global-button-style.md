# 0008 — Solid-green buttons everywhere, with a PillNav-style hover

**Status:** Done
**Date:** 2026-09-14

## Goal
Give every `.btn` in the dashboard the same solid deep-green treatment the analytics segmented
control already had, and add the PillNav hover: a white circle rising from the bottom edge to fill
the pill, with the label swapping colour as it arrives.

## Context
The analytics switcher was restyled to solid `--color-accent` with white text in plan 0005's bridge;
the owner wants that to be the button language across the whole app. Separately, the requested hover
came from the `PillNav` React component (GSAP timeline, a `.hover-circle` span and a duplicated
`.pill-label-hover` span per pill).

That component cannot be dropped in as-is: `UI/` is a pinned export and its DOM belongs to React, so
adding per-button child spans from the bridge risks React clobbering them (or throwing on
`removeChild`) the next time it updates that subtree. GSAP is also not loadable here.

## Approach
All of it is CSS in the bridge's injected stylesheet — no DOM restructuring, no new dependency.

- **Rest state:** `.btn { background: var(--color-accent); color: #fff; border-color: var(--color-accent) }`.
- **The rising circle** is `.btn::before` — a `165%`-wide circle centred on the bottom edge, `scale(0)`
  at rest, `scale(1)` on hover/focus-visible, clipped by `overflow: hidden` on the button. `isolation:
  isolate` plus `z-index: -1` puts it above the button's own background and below its label, which is
  what makes the label legible as the circle passes under it. `.btn-icon` gets a wider circle (`260%`)
  because a square button's corners sit further from the bottom-centre origin.
- **The label** transitions to `var(--color-accent)` with a 60ms delay so the colour lands as the
  circle arrives, rather than ahead of it.
- **`!important` throughout**: several buttons carry inline colour from the design's own `renderVals()`
  (the trend Like/Save buttons turn accent-coloured once liked/saved), and inline styles otherwise win.
- **Sidebar carve-out:** the sidebar *is* `--color-accent`, so a solid-green button would disappear
  into it. `.dc-sidebar .btn` keeps a transparent fill and a light border; the hover circle still runs.
- **`prefers-reduced-motion: reduce`** drops the transitions — hover still swaps to white, it just
  arrives without the rise.

## Decisions and trade-offs
- **Hover text is deep green on the white circle, not white.** The request said "white background with
  white text on hover", which would be invisible; this is the readable inverse, and the PillNav
  default does the same (dark pill + light label → light circle + dark label). One line to flip if the
  literal reading was intended.
- **No sliding label.** PillNav slides the old label out and a duplicate in, which needs the two extra
  spans per pill. Dropped for the reason above; the circle plus the colour swap carries the effect.
- **Liked/saved trend buttons lose their colour cue** (they were accent text on white; now every button
  is white text on accent). The glyph still changes: ♡→♥ and ＋→✓.

## Outcome
Built as planned in `dashboard/bridge.js`; nothing under `UI/` changed. Verified `node --check` and
that the generated stylesheet is brace-balanced with the intended cascade order. Not verified in a
browser — no environment here to run the app (see plan 0006).
