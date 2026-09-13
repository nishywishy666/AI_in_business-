# 0001 — The spec's default context path is a Firestore collection, not a document

**Date:** 2026-09-13
**Area:** backend / Firestore

## What happened
The first namespace test wrote the parent questionnaire to the spec's default `CONTEXT_PATH` (`users/{userId}/context`) and the in-memory backend rejected it: the path has 3 segments. In Firestore, paths alternate collection/document, so an odd segment count is a *collection* path and cannot hold fields directly.

## Root cause
The spec (§2.2, §5) writes `users/{userId}/context` as if it were a document. It also says the real path is unconfirmed ("Confirm the exact context path with the main-dashboard owner before first deploy"). Our own layout in `db/paths.py` was checked for even segment counts, but the parent's path was not.

## Fix
- `MemoryBackend` no longer enforces segment parity (it is a test double).
- `FirestoreBackend.get()` treats an odd-segment path as a collection and returns its first document, so the default still works if the parent stored a single context doc there.
- `CONTEXT_PATH` remains an env override for the real path.

## How to avoid next time
Before the first live deploy, ask the dashboard owner for the exact context location (a document path with an even number of segments, e.g. `users/{uid}/context/profile`, or a field on `users/{uid}`) and set `CONTEXT_PATH` accordingly. Any time a spec gives a Firestore path, count the segments.
