# 0003 — The μ-law encoder bias is added in the 14-bit domain, not 16-bit

**Date:** 2026-09-13
**Area:** voice / audio (services/voice/audio.py)

## What happened
The hand-rolled μ-law encoder disagreed with CPython's `audioop.lin2ulaw` on ~1 in 256 samples (off by one at segment boundaries). The decoder table was already correct.

## Root cause
G.711's reference encoder (`st_14linear2ulaw`) first shifts the 16-bit sample to 14 bits (`>> 2`), then adds `BIAS >> 2` (= 33). I added the full `BIAS` (0x84 = 132), which is the decoder's constant in the 16-bit domain. Same symbol, two domains.

## Fix
`pcm = min(|pcm14|, 8159) + (0x84 >> 2)` before the segment search. Verified against `audioop` for the whole int16 range in `tests/test_audio.py`.

## How to avoid next time
When porting a codec, keep the cross-check against a reference implementation in the test suite (here `audioop`, which still exists on Python ≤ 3.12) so a domain-mismatch bug is caught by the test, not on the phone. On 3.13+ that test skips — keep the fixture-based round-trip test as the floor.
