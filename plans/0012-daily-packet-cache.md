# 0012 — One packet a day instead of 400,000 reads

**Status:** Done
**Date:** 2026-09-14

## Goal
Stop the dashboard exhausting Firestore's free read quota. Fetch the business tree **once**, as a
single packet, cache it on disk, and take it apart locally on every read after that.

## The arithmetic
`FirestoreSource._load()` read a call's turns as a subcollection query **per call**, inside the loop
over calls. For the Uncle Tony dataset — 26 calls at ~8 turns — one snapshot cost roughly:

| | reads |
| --- | --- |
| calls | 26 |
| turns (one query per call) | ~208 |
| bookings, callbacks, emailsSent, unanswered, settings | ~40 |
| **per load** | **~275** |

The snapshot cached for 15s while the bridge polled every 60s, so an open tab forced a fresh load
every minute: **275 × 60 × 24 ≈ 396,000 reads/day** against a 50,000/day free tier. One browser tab,
nobody touching it, quota gone in about three hours. Moving to a database that does not meter reads
would have hidden this rather than fixed it — the app would still be pulling every transcript in the
business once a minute to render a table that shows transcripts only for the row you expanded.

## Approach
The marketing agent already works this way (`jobs/daily_pull.py` + its `LocalCache`); this gives the
voice tree the same treatment.

- **`dashboard/packet.py`** — `PacketStore`: one JSON file per business, written atomically through a
  temp file, with an age check. Every failure is survivable: a missing, unreadable or stale packet
  reads as "no packet", and an unwritable directory only costs the next read a live fetch. Defaults
  to `DASHBOARD_PACKET_DIR` or a temp dir, since Vercel's filesystem is read-only outside `/tmp`.
- **The packet is raw provider documents**, not parsed records — `{"calls": [{"id", "doc", "turns":
  [...]}], "bookings": [...], ...}`. Breaking it down runs the same `from_doc` parsing the live read
  used, so there is one definition of what a call is and an older packet still reads.
- **`FirestoreSource`** splits into `_fetch_packet()` (the only code that spends reads) and
  `_unpack()` (free). `load()` serves the packet while it is inside the day; `refresh()` forces a
  pull, exposed as `POST /api/dashboard/records/refresh`.
- **Writes patch the packet in place** rather than invalidating it: closing a callback, reviewing a
  question or saving a setting costs one write and *no* reads, and the day's packet stays true.
- `DASHBOARD_PACKET_HOURS` tunes the window (0 = always live, for debugging).

Reads go from ~396,000/day to ~275 plus whatever manual refreshes the owner asks for — about **0.5%**
of the free tier instead of 800% of it.

## Outcome
`PacketStore` was exercised directly here: round trip, the 23h/25h freshness boundary, patch-merge and
patch-append, a raising patch leaving the packet intact, and an unwritable directory returning False
without throwing. `tests/test_dashboard_packet.py` covers the source end: the tree read once and
twenty further loads costing nothing, a stale packet re-fetched, a write patching instead of
re-fetching, `refresh()` pulling again, and a read-only disk still serving data — with a fake client
that counts reads the way Firestore bills them. **Those tests have not been run** (no venv here, plan
0006).

## Follow-up worth doing
The fan-out itself is still in `_fetch_packet()` — it is just paid once a day now. Loading a call's
turns only when its row is expanded would cut even that by ~90%, and would matter if the call log
grows past a few hundred.
