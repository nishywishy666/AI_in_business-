# 0008 — A booking that bailed out logged the caller's last words as the callback "question"

**Date:** 2026-09-14
**Area:** voice / data

## What happened
Auditing the seeded production Firestore before the demo found 9 of 30 callbacks with reason `other`
whose question was `""`, `"Yes, that's right."` or a caller's email address. The Callbacks screen
rendered them verbatim. The same seed also logged the same three questions every day for a week, so
30 callbacks sat open with the oldest waiting six days, and replayed one booking onto the same table
and time on two different calls.

## Root cause
`BookingMachine._retry` and the three slot-full/slot-missing paths called `_to_callback` with the
raw `text` of the turn (or `""`). That is right when the caller asks for a human — their words are
the request — but wrong when the machine gives up on its own: the last utterance is a confirmation
or an email, not a question. The seed scenarios then repeat daily by design, and nothing closes them.

## Fix
- `_booking_note(slot, why)` writes a real note ("Wanted to book 4 people · 2026-09-14 · 12:30 —
  the party size could not be captured. Call back to finish the booking.") on every machine-initiated
  callback; the caller-asked path still keeps their words.
- The 9 junk rows were deleted from Firestore, 17 daily duplicates were marked `done`, and the older
  duplicate booking was moved to its own day. Both the seed's calls carry the `CAsim` prefix, so the
  cleanup refused to run if any real call had been present.

## How to avoid next time
Read the seeded data back through the dashboard payload (`/api/dashboard/bootstrap`) and look at the
Callbacks rows, not just the counts, before calling a demo environment ready. Any string that ends up
in an owner-facing row should be built by the engine, never copied from a transcript turn.
