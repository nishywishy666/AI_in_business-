# Known limitations (vr_plan.md §16) — say these before a judge finds them

1. **Calls are cut and re-streamed at 280 seconds** by Vercel Hobby's 300 s function ceiling. State survives; the caller hears a 1–2 second pause. Removed by a paid plan or a persistent host.
2. **8 kHz telephony audio** measurably degrades transcription of spelled strings and unusual item names. The email-capture design (§9) exists because of it.
3. **Caller conversations are processed by free-tier Gemini**, which may use content to improve Google's products and may be human-reviewed. Disclosed in the greeting. Move to a paid tier (~half a cent per call) before ongoing real use.
4. **Allergens are refused unless the owner has confirmed them.** Deliberate. The refusal is the feature.
5. **Ordering and upselling are out of scope.**
6. **Capacity is seats per service window, not per table.** A 40-seat room cannot really seat ten parties of two at once.
7. **One vertical** — restaurant only.
8. **Email confirmation can fail silently** if the address is mis-transcribed past two spelling attempts. The booking survives and is flagged for owner follow-up; the agent does not claim an email was sent.
9. **No SMS, no outbound calls, no live human transfer.** Callbacks are the escalation path.
10. **Single tenant.** Every document carries `businessId` so this is configuration later, not a rewrite.

## Note for the owner (§7.4)
Free-tier processing of caller conversations is acceptable for a demo with a deliberate disclosure. Before this system takes real customer calls on an ongoing basis, move Gemini to a paid tier — it is roughly half a cent per call and it is the difference between disclosed third-party training and none.
