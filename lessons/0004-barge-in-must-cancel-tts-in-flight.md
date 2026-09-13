# 0004 — Barge-in must fire while TTS is still in flight, not only once audio has been sent

**Date:** 2026-09-13
**Area:** voice / media pipeline (services/voice/call_pipeline.py)

## What happened
The barge-in test talked over the agent ~10 ms after a turn finished, while the (deliberately slow) TTS request was still pending. No `clear` was sent and the interruption was not recorded.

## Root cause
The barge-in condition was `VAD fired AND session.agent_speaking`, and `agent_speaking` only becomes true when the first outbound media frame is sent. ElevenLabs' first byte takes 75–150 ms (vr_plan.md §4.1), so a caller who interrupts inside that window was ignored and the agent would then talk over them.

## Fix
Treat "TTS task in flight" as agent-busy too: `agent_speaking OR (_tts_task and not done)`. The interruption then cancels the in-flight request (§6.5 step 2) and sends `clear`.

## How to avoid next time
When a state flag is derived from an *output* event, list the windows before that output exists (request in flight, tokens queued) and decide explicitly what each one should do. Test the interrupt with a delayed fake provider, not an instant one.
