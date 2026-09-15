# 0010 — A bare "yes" mid-booking is a control signal, not an intent to classify

**Date:** 2026-09-14
**Area:** voice engine (`services/voice/engine.py`), booking machine (`services/voice/booking_machine.py`)

## What happened
A full booking ran correctly to the confirm step — "So that's a table for 4 on Thursday at half past twelve, under Keith Andre. Shall I lock that in?" — and then the caller's "Yes that's correct" produced *"I'm not sure about that one — I'll check with the owner and have them call you back."* The booking never committed, and a junk callback was filed against the caller's own confirmation.

## Root cause
`engine.run_turn` only forwarded a turn to the booking machine when the router returned `BOOK` (or `CALLBACK` mid-booking). The router labels a bare affirmative as `ANSWER_QUESTION`, so it fell through to the answerer, found nothing to answer, and degraded to a callback. Measured at stage=confirm:

| caller says | router intent |
|---|---|
| "yes" / "yes please" / "Yes that's correct" | `ANSWER_QUESTION` |
| "correct" / "yep lock it in" | `BOOK` |

`booking_machine` already handled this correctly — `if is_yes(text): self._commit(...)` — it simply never got the turn. The most natural way to say yes, at the single most important moment in the call, silently lost the booking.

## Fix
While `slot_state.stage` is an active booking stage, a deterministic yes/no goes to the booking machine regardless of the router's label (R4: decisions in Python, not in a model). `is_yes`/`is_no` already discriminate properly — "do you have parking" is neither, so genuine mid-booking questions still reach the answerer.

## How to avoid next time
When a state machine is mid-flight, control words ("yes", "no", "stop", "go back") belong to the machine, not to an intent classifier. Ask which utterances are *control* rather than *content* before letting a model arbitrate them — and test the happy path with the most ordinary possible phrasing, not the most descriptive one. The scripted test used "yep lock it in", which happened to classify as `BOOK` and hid the bug.
