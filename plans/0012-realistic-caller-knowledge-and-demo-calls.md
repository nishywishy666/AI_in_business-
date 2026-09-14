# 0012 — Realistic caller knowledge for the receptionist, and a demo week that exercises it

**Status:** Done
**Date:** 2026-09-14

## Goal
Firestore should hold what a toastie shop actually gets asked on the phone, so the Voice AI has
references to pull from and the demo call log shows those questions being answered — not the same
three questions repeated every day.

## Knowledge (`data/business/uncle_tony/facts.json` → `businesses/uncle_tony/facts/*`)
18 new fact documents, all owner-unverified plausible defaults: parking, bookings policy, delivery,
takeaway, catering, dietary, kids, dogs, coffee, accessibility, payment, public holidays, wifi, gift
vouchers, loyalty, alcohol/BYO, wait time, jobs. Each has synonyms in `config/fact_synonyms.yaml`.
Specific keys sit **before** `hours`/`address`/`phone`/`payment` because the first synonym hit wins:
"are you open on public holidays" must reach `public_holidays`, not `hours` via "open", and "gift
card" must reach `gift_vouchers`, not `payment` via "card". Deliberately left as gaps (they show the
review flow): gluten-free, dairy-free, oat milk, nut-free — anything allergen-shaped with no confirmed
allergen entry still goes to a callback (Rule 2).

## Demo calls (`scripts/seed_demo_calls.py --profile realistic`)
A pool of ~30 scenarios — bookings with different names/parties/times, answerable questions across
the new facts, allergen gaps, catering/complaint/large-group callbacks, and chitchat — rotated by
day so a week has variety and the recent two days are fuller. Distinct caller numbers per persona.
`--profile mockup` (the default for `seed()`, used by the dashboard tests) keeps the original
eight-call day so the pinned test numbers stay meaningful; the CLI defaults to `realistic`.
`--reset` now also works with `--sink firestore`: it deletes every `CAsim*` call (with its turns),
the callbacks/bookings/emails that reference one, and the rollups — but only if **every** call in the
tree is a demo call, so it can never touch a real one.

## Consequences
- `Do you do delivery?` is now answered from a fact, so the mockup-profile seed has one fewer
  "Couldn't answer" and the dashboard route tests' pinned numbers move with it.
- The research packet on the Business Context screen still shows the six mockup fields; the new facts
  are what the receptionist reads, not extra rows.
