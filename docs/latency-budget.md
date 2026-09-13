# Latency budget (vr_plan.md §4.1)

Per-turn targets. The simulator HUD draws each turn's stage timings against these.

| Stage | Target | Note |
| --- | --- | --- |
| Twilio ↔ syd1 network | 40–90 ms | each way |
| Silero VAD | ~0 ms | audio thread |
| Smart Turn v3.2 | 10–100 ms | 8 MB int8 ONNX |
| Scribe final | 150–300 ms | 8 kHz input degrades accuracy, not speed |
| Groq route + extract | 100–250 ms | small prompt, 560 tok/s class |
| Gemini phrase (Q&A turns only) | 300–600 ms | skipped entirely on booking turns |
| ElevenLabs Flash first byte | 75–150 ms | `ulaw_8000`, no resample needed |
| **First audio — booking turn** | **0.4–0.8 s** | template reply, no Gemini |
| **First audio — Q&A turn** | **0.7–1.4 s** | with Gemini |
| Firestore transaction (booking write) | 100–300 ms | needs a spoken filler |

If a Q&A turn exceeds 1.4 s, the cause is almost always Gemini queueing on the free tier. Stage timings are logged per turn (`sttMs`, `routerMs`, `answerMs`, `ttsMs` on the turn document) so this is diagnosable rather than guessable.
