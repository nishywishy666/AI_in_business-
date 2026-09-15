# 0011 — A fact in Firestore with no synonym entry is invisible to callers

**Date:** 2026-09-14
**Area:** voice lookups (`config/fact_synonyms.yaml`, `services/voice/tools.py`)

## What happened
`businesses/uncle_tony/facts` held 27 well-written answers — wifi, payment, dogs, kids, catering, accessibility, takeaway, public holidays — and the receptionist could answer questions about **eight** of them. Asking "do you have wifi?" produced "I'll have the owner call you back", with the answer sitting in Firestore the whole time.

## Root cause
`answer_question()` matches a question to a fact by walking `config/fact_synonyms.yaml`, and only then reads `facts/{fact_key}`. That YAML file had synonyms for 8 keys. Seeding a fact does nothing on its own — the synonym list is the only index into the collection, so the two have to be edited together and nothing enforced that.

The failure is silent in the worst way: the data looks present in the console, the lookup reports `no_data`, and it degrades to a polite callback rather than an error.

## Fix
Synonyms for every caller-facing key (19 added), ordered most-specific-first because matching is substring-based and first-hit-wins — `gift card` and `loyalty card` both contain `payment`'s `card` synonym, so they must be listed above it. `tests/test_business_dataset.py::test_every_caller_facing_fact_is_reachable_by_some_question` now fails if a fact is added without a way to ask for it.

Two related traps found while doing it:
- Adding `gluten free` / `dairy free` to the `dietary` synonyms hijacked allergen questions away from the grounded allergen path (V9 "never guess"), turning a careful "I can't confirm that" into a generic marketing line. Allergen wording must never appear in a fact synonym list; there is now a test for that too.
- A fact whose value is already a sentence was being wrapped by the template into `"Closed Sunday. on today."` and `"...directly.."`. Templates now only wrap values that are not already sentences.

## How to avoid next time
When a lookup is driven by a hand-maintained mapping, add the test that the mapping covers the data before adding the data. And when broadening a matcher, ask what it *steals* from the matchers below it — the cost of a new synonym is paid by every rule after it.
