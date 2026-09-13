# BUILD PROMPT — AI Voice Receptionist 
## 0. Read this first

You are building **one subsystem**: the AI voice receptionist that answers real phone calls for a restaurant, plus a local test simulator for it. You are **not** building the marketing agent (already built) or the dashboard (in progress by someone else).

### Rules you must follow

| Rule | Detail |
| --- | --- |
| **R0 — Never invent a requirement** | If something you need is not in this document, stop and emit `TODO(spec):` naming exactly what is missing. Do not choose for me. This applies hardest to prompt text, thresholds, field names and pricing. |
| **R1 — Build in phase order** | §13 has five phases with acceptance tests. Do not start phase N+1 until phase N's tests pass. |
| **R2 — Do not touch existing code** | The marketing agent and dashboard pages already exist. Read them to match conventions. Do not refactor, rename or "improve" them. If the voice code needs something they own, add a new module rather than editing theirs. |
| **R3 — No secrets client-side** | `FIREBASE_SA_JSON`, `TWILIO_AUTH_TOKEN`, `GROQ_API_KEY`, `GEMINI_API_KEY`, `ELEVENLABS_API_KEY` are server-only. A client bundle importing any of them is a build failure. |
| **R4 — Arithmetic in code, never in a model** | No model computes a seat count, a total, or an availability decision. Models classify, extract and phrase. Python decides. |
| **R5 — Typed boundaries** | Pydantic models in `contracts/voice.py`. Every model output is parsed into a Pydantic model before use. An unparseable model response is a handled error path, not an exception that kills the call. |
| **R6 — Every log line carries `call_id`** | Plus `turn_index` where applicable. Structured JSON logs. |
| **R7 — The simulator must exercise the production code path** | Not a parallel implementation. See §12 — this is the single most important design constraint in the document. |

### What already exists (do not rebuild)

- Firebase project: Firestore + Storage + Auth, `australia-southeast1`.
- Firestore collection tree under `businesses/{businessId}` — `menuItems`, `facts`, `capacitySlots`, `bookings`, `callbacks`, `calls/{callId}/turns`, `unanswered`, `usageEvents`, `emailsSent`, `rollups`, and the `marketing/*` subtree.
- The marketing scan pipeline and its Gemini ladder.
- Dashboard pages (in progress).
- `services/common/firestore.py`, `services/common/config.py`, `services/common/logging.py` — reuse these. If they do not exist yet, create them to the shapes in §11 and §14.

### What you are building

A FastAPI service that answers a Twilio phone call, holds a grounded conversation, books a table into Firestore, mirrors it to Google Calendar, emails a confirmation, and logs everything for the dashboard — plus a localhost simulator that drives the identical pipeline from a browser without placing a phone call and without writing anything to the production database.

---

## 1. Locked decisions

Do not re-open these.

| # | Decision |
| --- | --- |
| V1 | **Telephony is Twilio Voice with bidirectional Media Streams.** Inbound calls only. No outbound dialling. |
| V2 | **Number type: Australian mobile number.** Chosen because AU *local* numbers require a regulatory bundle with government ID and proof of address uploaded and approved; AU mobile needs only name and address. |
| V3 | **Two LLMs, two jobs.** Groq `llama-3.1-8b-instant` does intent routing and field extraction. Gemini Flash does conversational answering. Neither does the other's job. |
| V4 | **Gemini runs on the free tier**, and the call greeting therefore discloses that third-party AI services process the call. See §7.4 — this is a deliberate, owner-accepted trade. |
| V5 | **STT and TTS are ElevenLabs.** Scribe v2 Realtime in, Flash v2.5 out with `output_format=ulaw_8000`. No local models anywhere — the target machine is an 8 GB M1 and cannot host them alongside everything else. |
| V6 | **An intent router exists.** It is a small schema-constrained Groq call, run once per caller turn, before any answering. |
| V7 | **Booking is a deterministic Python state machine.** The LLM extracts one field at a time. The LLM never calls a six-argument `create_booking` tool. |
| V8 | **Firestore is the booking source of truth. Google Calendar is a one-way mirror.** Concurrency via a Firestore transaction over a denormalised `seatsBooked` counter on the slot document. |
| V9 | **The agent reads only confirmed data.** `menuItems` where `confirmedByOwner == true`; `facts/{factKey}` documents (existence means confirmed); allergen map entries where `confirmed == true`. Anything else is unknown and routes to a callback. |
| V10 | **Ordering and upselling are out of scope.** Three outcomes only: answer a question, book a table, take a callback. |
| V11 | **Deployed on Vercel (Hobby), region `syd1`.** §3 lists every constraint that imposes and the required response to each. |
| V12 | **The simulator writes to local JSONL files, never to production Firestore.** Isolation is structural, not a `where` filter. |

---

## 2. Non-negotiable rules

**Rule 1 — Grounding.** Hours, prices, ingredients, allergens, address and phone reach a caller only from a confirmed Firestore document. No model generates one. A lookup returning nothing means the agent says it will check and takes a callback. There is no third behaviour.

**Rule 2 — Allergens.** An allergen map entry with `confirmed != true`, or absent from the map, is treated as unknown: refuse and escalate with `reason='allergen_unknown'`. Every allergen answer that *is* given has the cross-contact line from `config/disclaimers.py` appended **by the tool, in Python, after the model has finished**. The model never writes that line and never omits it.

**Rule 3 — Disclosure.** The first agent utterance discloses that the caller is speaking to an AI assistant, that the call is recorded, and that third-party AI services process it. Text lives in `config/disclaimers.py`, is played from a pre-rendered audio file, and is not skippable or model-generated.

**Rule 4 — The answering model gets facts, never a database.** Gemini receives a retrieved fact plus a short history and phrases it. It has no tools, no Firestore access, and no ability to look anything up.

**Rule 5 — Idempotency is a document ID.** `bookings/{idempotencyKey}`, `emailsSent/{idempotencyKey}`, written with `create()` so a duplicate fails loudly. Never `add()` for anything a retry could duplicate.

**Rule 6 — A model failure never drops the call.** Every model call has a timeout, one retry, and a deterministic fallback utterance. If Groq returns unparseable JSON twice, route to `CALLBACK`. If Gemini times out, speak the retrieved fact with a template instead of a generated sentence. The caller must never hear silence.

**Rule 7 — No recitation.** The agent never lists the menu. A section name and at most three items, then ask what they are after.

---

## 3. Platform constraints (Vercel Hobby) and the required response

| Constraint | Value | Required response |
| --- | --- | --- |
| Max function duration | **300 s**, default and hard ceiling on Hobby | §6.6. The server self-terminates the stream at **280 s** and Twilio re-enters the webhook via `<Redirect>`, opening a fresh stream on the same `CallSid` with state reloaded from Firestore. |
| Region | Defaults to `iad1` | `"regions": ["syd1"]` in `vercel.json`. Confirm in function logs. Worth ~200–250 ms per turn. |
| WebSockets | Supported; requires Fluid compute (default on new projects) | FastAPI ASGI with `uvicorn[standard]`. A connection is pinned to one instance for its lifetime, so per-call in-process state is safe. |
| Instance affinity | Not guaranteed across connections | No shared in-memory cache. Load business context once per call at stream start. Cross-connection state lives in Firestore, keyed on `CallSid`. |
| Memory / CPU | 2 GB / 1 vCPU | Silero VAD and Smart Turn v3.2 run on **ONNX Runtime**. `torch` must not appear in `requirements.txt`. |
| Python bundle | 500 MB uncompressed | Achievable without torch. If exceeded, trim before reaching for `VERCEL_SUPPORT_LARGE_FUNCTIONS`. |
| Cold start | Seconds, and it lands on the first caller after idle | Load ONNX models at **module scope**. Play the pre-rendered greeting WAV immediately on `start` so first audio never waits on model load, provider handshake or a Firestore read. `scripts/warm.sh` before any demo. |
| Firestore reads | 50k/day (Spark) | Load business context once per call, not per turn. Metrics come from `rollups/`, never from scanning `calls/`. |
| Firestore writes | 20k/day (Spark) | **Partial transcripts are never written.** One write per final turn. |

---

## 4. Call flow

```
  Caller dials the AU mobile number
        │
        ▼
  Twilio ──HTTP POST──► /api/voice/incoming
        │               · validate X-Twilio-Signature (§6.2)
        │               · return TwiML: <Connect><Stream/></Connect><Redirect/>
        ▼
  Twilio ──WSS──────────► /api/voice/ws
        │  connected → start → media… → stop
        │
        │  on `start`:
        │    · validate the signed token from <Parameter> (§6.3)
        │    · load business context ONCE (menu index, facts, windows)
        │    · create or resume calls/{callId}
        │    · push the pre-rendered greeting immediately
        ▼
  ┌─ per 20 ms inbound frame ──────────────────────────────────┐
  │  base64 → μ-law 8k → PCM16 8k → resample 16k (§6.4)        │
  │      ▼                                                     │
  │  Silero VAD (ONNX)         is anyone speaking              │
  │      ▼                                                     │
  │  Smart Turn v3.2 (ONNX)    have they finished              │
  │      ▼                                                     │
  │  ElevenLabs Scribe         partials → simulator/log only   │
  │                            final   → the turn begins       │
  └────────────────────────────────────────────────────────────┘
        ▼
  ┌─ per caller turn ──────────────────────────────────────────┐
  │  1. GROQ llama-3.1-8b-instant  (§7.1)                      │
  │     schema-constrained: intent + confidence + one field    │
  │     ~150 ms                                                │
  │                                                            │
  │  2a. intent = BOOK / CALLBACK                              │
  │      → Python state machine (§8) produces the next          │
  │        utterance from a TEMPLATE. No Gemini call.           │
  │        Fast, deterministic, cannot hallucinate.            │
  │                                                            │
  │  2b. intent = ANSWER_QUESTION                              │
  │      → tool lookup in Firestore (§10)                      │
  │      → found:   GEMINI Flash phrases the retrieved fact    │
  │                 into one or two sentences (§7.2) ~400 ms   │
  │      → missing: Python template says it will check,         │
  │                 then hands to the callback machine         │
  │                                                            │
  │  2c. intent = CHITCHAT → GEMINI, one short line, no facts  │
  │  2d. intent = END      → closing template, then hang up    │
  └────────────────────────────────────────────────────────────┘
        ▼
  ElevenLabs Flash v2.5, output_format=ulaw_8000
        │  stream out clause by clause
        ▼
  {"event":"media","streamSid":…,"media":{"payload":base64}}
        │  + a `mark` after each utterance so we know it played
        │  + {"event":"clear"} the instant VAD fires (barge-in, §6.5)
        ▼
  Caller hears the reply
```

The important property: **most turns cost one Groq call and nothing else.** Booking turns never touch Gemini, so the expensive, slower model is only paid for when the caller actually asks a question. This is what keeps a telephony-grade latency budget achievable with a free-tier answering model.

### 4.1 Latency budget

Write this into `docs/latency-budget.md` and surface it in the simulator HUD.

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

If a Q&A turn exceeds 1.4 s, the cause is almost always Gemini queueing on the free tier. Log the stage timings per turn so this is diagnosable rather than guessable.
---

## 5. Repository additions

Add only these. Do not restructure what exists.

```
api/voice/
  incoming.py            # POST  /api/voice/incoming    → TwiML
  ws.py                  # WSS   /api/voice/ws          → the media stream loop
  status.py              # POST  /api/voice/status       → Twilio status callbacks
  fallback.py            # POST  /api/voice/fallback     → TwiML said when we are down
api/sim/
  app.py                 # local only, refuses to mount unless ENABLE_SIM=1

services/voice/
  twiml.py               # TwiML builders, one function per response shape
  signature.py           # X-Twilio-Signature validation + WS token mint/verify
  audio.py               # μ-law ⇄ PCM16, 8k ⇄ 16k, framing
  stream.py              # Twilio WS envelope parse/serialise (media, mark, clear)
  pipeline.py            # Pipecat assembly; transport-agnostic
  session.py             # per-call state, load-once context, resume, 280s cutover
  router.py              # §7.1  Groq intent + field extraction
  answerer.py            # §7.2  Gemini phrasing
  booking_machine.py     # §8    deterministic slot-fill
  email_capture.py       # §9    normalise, score, spell-out escalation
  tools.py               # §10   the five lookups
  templates.py           # every deterministic utterance, in one file
  telemetry.py           # per-stage timings, per-turn write
  sinks.py               # FirestoreSink | LocalJsonlSink  ← §12 isolation

config/
  disclaimers.py         # greeting disclosure + cross-contact line
  capacity.yaml          # service windows and seat counts
  allergens.yaml         # the AU declarable allergens
  au_email_domains.yaml  # §9 fuzzy-match list
  thresholds.yaml        # every tunable number, named, in one place

contracts/voice.py       # Pydantic: RouterOutput, SlotState, EmailCandidate, TurnLog

tests/
  conversations/*.yaml   # scripted caller turns, §13
  test_twilio_stream.py
  test_signature.py
  test_audio.py
  test_router.py
  test_booking_machine.py
  test_email_capture.py
  test_booking_concurrency.py
  test_grounding.py
  test_sim_isolation.py  # proves the simulator cannot write to Firestore

scripts/
  render_greeting.py     # pre-renders the disclosure WAV once, at build time
  warm.sh
  tunnel.sh              # cloudflared/ngrok helper for local Twilio testing
```

---

## 6. Twilio integration

This section is the part most likely to be built wrong. Every shape below is from Twilio's current documentation — use them exactly.

### 6.1 The webhook and the TwiML

`POST /api/voice/incoming` receives **form-encoded** parameters, not JSON. The ones that matter: `CallSid`, `From`, `To`, `CallStatus`, `AccountSid`, `Direction`.

Return this, with `Content-Type: text/xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://YOUR_HOST/api/voice/ws"
            statusCallback="https://YOUR_HOST/api/voice/status">
      <Parameter name="token" value="SIGNED_TOKEN"/>
      <Parameter name="callSid" value="CA..."/>
      <Parameter name="businessId" value="..."/>
      <Parameter name="resume" value="0"/>
    </Stream>
  </Connect>
  <Redirect method="POST">/api/voice/incoming?resume=1</Redirect>
</Response>
```

Three things about this that are load-bearing:

1. **`<Connect><Stream>` is bidirectional.** `<Start><Stream>` is a one-way fork and cannot send audio back. Use `<Connect>`.
2. **`url` must be `wss://`.** `ws://` is rejected.
3. **The `<Redirect>` after `</Connect>` is the 300-second mitigation.** Twilio's documented behaviour: *"Twilio executes the remaining TwiML instructions only after your server closes the WebSocket connection."* So when we close the socket at 280 s, Twilio re-POSTs the webhook, gets a fresh `<Connect><Stream>`, and the caller stays on the same `CallSid` throughout. See §6.6.

Configure in the Twilio console: the number's **A CALL COMES IN** webhook → `POST /api/voice/incoming`, and **PRIMARY HANDLER FAILS** → `POST /api/voice/fallback`, which returns a `<Say>` apologising and a `<Hangup/>`. Without the fallback, a deploy error gives the caller dead air.

### 6.2 Signature validation — mandatory

```python
from twilio.request_validator import RequestValidator

validator = RequestValidator(TWILIO_AUTH_TOKEN)
ok = validator.validate(url, post_params_dict, request.headers["X-Twilio-Signature"])
```

**The gotcha that will cost you an hour:** the `url` must be the exact public URL Twilio called, including scheme, host and query string. Behind a tunnel or Vercel's proxy, reconstructing it from the request object gives you the wrong scheme or an internal host, and validation fails on every request. **Build the URL from a `PUBLIC_BASE_URL` env var plus the path and raw query string.** Never from `request.url`.

Reject with 403 on failure. Log the rejection.

### 6.3 WebSocket authentication — the hole most tutorials leave open

**Twilio does not sign the WebSocket connection.** Only the HTTP webhook is signed. So `wss://YOUR_HOST/api/voice/ws` is an open endpoint: anyone who learns the URL can connect and burn your ElevenLabs and Gemini credits, or drive the booking machine.

Close it: in `/api/voice/incoming`, mint a short-lived HMAC token over `(CallSid, expiry)` using a server secret, pass it as `<Parameter name="token">`, and in the `start` message verify it — signature valid, not expired (60 s TTL), and `CallSid` matching the one in the same `start` payload. Reject and close the socket otherwise. Cache used tokens for the call's lifetime so a replay after the call cannot open a new stream.

### 6.4 Audio

**Inbound.** Twilio sends `audio/x-mulaw`, `8000` Hz, mono, 20 ms per frame (160 bytes), base64 in the `media.payload` field.

```
base64 decode → μ-law bytes → PCM16 8 kHz → resample to 16 kHz
                                            → Silero VAD, Smart Turn, Scribe
```

**The Python 3.13 trap:** the `audioop` module was removed from the standard library in Python 3.13. If you target 3.13+, either add `audioop-lts` to `requirements.txt` or implement μ-law decoding with a 256-entry numpy lookup table and resampling with `scipy.signal.resample_poly`. Pick one, write it in `services/voice/audio.py`, and unit-test it against a known μ-law fixture in `test_audio.py`. Do not discover this at deploy time.

**Outbound.** Request ElevenLabs with `output_format=ulaw_8000`. The bytes come back already in Twilio's format — no resampling, no conversion, no quality loss from a double transcode. Base64 them and send:

```json
{"event":"media","streamSid":"MZ...","media":{"payload":"<base64 μ-law>"}}
```

`streamSid` is required on every outbound message. Chunk at roughly 20 ms; Twilio buffers, so exact framing is not critical, but do not send one enormous blob — it defeats barge-in.

**Quality note to carry into the spec, not to fix:** 8 kHz telephony audio measurably degrades STT on spelled-out strings and unusual menu-item names. This is why §9 exists in the shape it does, and why it is not over-engineering.

### 6.5 Barge-in

The moment Silero VAD fires while agent audio is outstanding:

1. Send `{"event":"clear","streamSid":"MZ..."}` — this discards audio Twilio has buffered but not yet played. Without it the caller keeps hearing the agent for a second or more after interrupting, which is the single most noticeable "this is a robot" tell.
2. Cancel the in-flight TTS request and drop any queued LLM tokens for that turn.
3. Record the interruption on the turn document.

Use **`mark` events to know what was actually heard.** Send a mark after each utterance chunk; Twilio echoes it back when that audio has finished playing. If a mark never returns, the caller was talked over or hung up — so the agent must not assume a question it asked was heard. This matters most for the email read-back in §9: never treat an unconfirmed read-back as confirmed just because you sent the audio.

Acoustic echo is **not** a problem here the way it was in the browser: the carrier handles it and `<Connect>` gives you the inbound track. Keep a 150 ms guard after agent speech starts to suppress trailing artefacts, and nothing more.

### 6.6 The 280-second cutover

```
t=0      stream opens, calls/{callSid} created or resumed
t=240s   agent says (template): "I might need to put you on hold for a
         second, but I've got everything so far."
t=280s   · write historySummary + slotState to calls/{callSid}
         · finish the current utterance if one is playing
         · close the WebSocket cleanly from the server side
         ↓
Twilio   executes the <Redirect> → POSTs /api/voice/incoming?resume=1
         → fresh <Connect><Stream>, same CallSid
t≈282s   new stream opens; start message carries resume=1
         · reload historySummary + slotState by CallSid
         · agent resumes mid-task: "Right — I had a table for four at seven,
           under Sarah. What was the best email for you?"
```

Close **proactively at 280 s** rather than waiting for Vercel to kill the function at 300 s: a clean server-side close is what makes Twilio move to the next verb promptly, so the silence gap is ~1–2 s instead of a hard failure.

Record `resumeCount` on the call document. If it exceeds 3, stop redirecting and close the call gracefully with a callback — something has gone wrong and looping a caller forever is worse than ending politely.

**State that must survive a cutover** (all on `calls/{callSid}`): `historySummary` (one paragraph, regenerated at cutover), `slotState` (§8), `emailCandidate` (§9), `resumeCount`, `disclosureDone` — the disclosure plays once per call, not once per stream.

### 6.7 Status callbacks

`POST /api/voice/status` receives stream lifecycle events (`stream-started`, `stream-stopped`, `stream-error`). Also configure call-level status callbacks on the number for `completed`. Use them to:

- Set `outcome` on the call document when a caller hangs up mid-turn (`abandoned`).
- Set `endedAt` and `durationMs` authoritatively from Twilio rather than guessing from the socket close.
- Log `stream-error` loudly — it is how you find out that a deploy broke the socket without any caller complaining.

### 6.8 Local development with Twilio

Twilio must reach your machine over a public `wss://` URL. `scripts/tunnel.sh` wraps `cloudflared tunnel --url http://localhost:8000` (preferred — no account needed for quick tunnels, more stable than free ngrok) and prints the `wss://` URL to paste into the Twilio console.

Set `PUBLIC_BASE_URL` to the tunnel URL so §6.2 signature validation works. The tunnel URL changes on every restart, so §6.2 must read it from the environment, not hardcode it. Put this in the README as a three-line checklist, because it is the step that silently breaks signature validation.

### 6.9 Cost and account notes

- AU mobile number: a few dollars a month; inbound around a cent a minute. Negligible.
- **A Twilio trial account plays a message before connecting and only calls verified numbers.** Upgrade before the demo, or the judges hear Twilio's trial notice before your greeting.
- Australia's Scam Prevention Framework is tightening KYC on numbers. Buy the number early; do not leave it to the week of the event.
---

## 7. The two models

### 7.1 Groq — intent routing and field extraction

Model: `llama-3.1-8b-instant`. 131k context, fast enough that a round trip is 100–250 ms. Tool/JSON support is native to the chat template, which is why this model and not a larger one without it.

Called **once per caller turn**, with a schema-constrained response. Use Groq's JSON mode / structured output so the response cannot be prose.

```python
# contracts/voice.py
class RouterOutput(BaseModel):
    intent: Literal["ANSWER_QUESTION", "BOOK", "CALLBACK", "CHITCHAT", "END"]
    confidence: float                      # 0.0–1.0
    question: str | None                   # verbatim, when intent=ANSWER_QUESTION
    field_name: str | None                 # the ONE field this turn supplied
    field_value: str | None                # its raw value, unparsed
    wants_human: bool                      # explicit ask for a person
```

**System prompt rules** (keep it under 400 tokens — it is sent every turn):

- Classify the caller's last turn into exactly one intent. Do not answer anything.
- Extract **at most one** field, and only if the caller just supplied it: one of `date`, `time`, `party_size`, `name`, `phone`, `email_raw`.
- Emit the field value **exactly as heard**. Do not normalise, correct, expand or reformat. Python does that.
- `confidence` is your confidence in the *intent*, not the field.
- If the caller mentions a complaint, a large group (over `LARGE_GROUP_THRESHOLD` from `thresholds.yaml`), catering, or asks for a person, set `intent=CALLBACK` and `wants_human` accordingly.
- Inject per call: the current date and time in `Australia/Melbourne`, and the current booking slot state so the model knows which field is being asked for. Nothing else. **Never the menu, never the facts.**

**Failure handling (Rule 6).** Parse into `RouterOutput`. On a parse failure: retry once with a repair instruction. On a second failure: `intent=CALLBACK`, `confidence=0.0`, and log it. Two unparseable responses in one call should raise a warning in the simulator HUD — it means the prompt has drifted.

**Low confidence.** Below `ROUTER_MIN_CONFIDENCE` (default 0.6, in `thresholds.yaml`), do **not** guess and do **not** ask a clarifying question on every turn — that reads as the receptionist not listening. Instead: if a booking is already in progress, assume the turn relates to the current slot; otherwise treat it as `ANSWER_QUESTION` and let the lookup miss route it to a callback naturally.

### 7.2 Gemini Flash — conversational answering

Model: pinned in `thresholds.yaml`; resolve it at startup by intersecting a preference list with `ListModels()` so a retired ID drops out instead of 404-ing mid-call. Preference order: `gemini-3.8-flash` → `gemini-3.6-flash` → `gemini-3.5-flash` → `gemini-3.5-flash-lite`. Never a `*-preview` ID. Never Gemini Pro.

Gemini's **only** job is turning a retrieved fact into one spoken reply. It has no tools and no database access (Rule 4).

Input it receives:
- The retrieved fact or menu item, as structured data.
- The last 4 turns of conversation, text only.
- The business name.

Instructions it is given:
- One or two sentences. Under `MAX_REPLY_WORDS` (default 40) — long replies cost TTS seconds and get interrupted.
- Use only the supplied fact. If it does not answer the question, say so plainly; do not fill the gap.
- Never state a price, time, ingredient or allergen that is not in the supplied data.
- Spoken register: contractions, no bullet points, no markdown, no emoji. This text goes straight to TTS.
- Never list more than three items (Rule 7).

**Timeout and fallback (Rule 6).** `GEMINI_TIMEOUT_MS` default 1200. On timeout or error, speak the fact through a template in `templates.py` instead — e.g. `"We're open {hours} on {day}."` The caller gets a flatter sentence and never a silence. Log the fallback; a rising fallback rate is the signal that free-tier Gemini is rate-limiting you.

**Free-tier reality (V4).** Free tier is per-project, per-model, resets midnight Pacific, and will 429 under load. Because booking turns never call Gemini (§4), a normal call makes only a handful of Gemini requests, so this is survivable — but the template fallback is not optional decoration, it is the thing that keeps the call alive when the quota runs out mid-demo.

### 7.3 What each model must never do

| | Groq router | Gemini answerer |
| --- | --- | --- |
| Answer the caller | never | yes, one reply |
| See the menu or facts | never | only the single retrieved item |
| Normalise a field value | never | never |
| Decide availability | never | never |
| Compute anything | never | never |
| Write to Firestore | never | never |
| Append the cross-contact line | never | never — Python does it |

### 7.4 The disclosure (Rule 3, V4)

Because Gemini runs on the free tier, where submitted content may be used to improve Google's products and may be reviewed by humans, the greeting must be honest about third-party processing. Put the exact text in `config/disclaimers.py` and pre-render it to audio with `scripts/render_greeting.py` so it costs no TTS credits per call and plays instantly:

```python
GREETING_DISCLOSURE = (
    "Thanks for calling {business_name}. You're speaking with an AI assistant, "
    "this call is recorded, and it's processed using third-party AI services. "
    "How can I help?"
)
CROSS_CONTACT = (
    "I should say that we prepare everything in the same kitchen, "
    "so we can't guarantee any dish is completely free of traces."
)
```

Play it once per call, tracked by `disclosureDone` on the call document so a 280-second cutover does not repeat it.

**Note for the owner, to go in `docs/known-limits.md`:** free-tier processing of caller conversations is acceptable for a demo with a deliberate disclosure. Before this system takes real customer calls on an ongoing basis, move Gemini to a paid tier — it is roughly half a cent per call and it is the difference between disclosed third-party training and none.

---

## 8. Booking — the deterministic state machine (V7)

The LLM never calls a six-argument booking tool. Small and mid-size models drift on multi-parameter calls, and a drifted booking is a real customer at a table that does not exist. Python owns the sequence; the model only extracts one field per turn.

### 8.1 Slot state

```python
class SlotState(BaseModel):
    stage: Literal["idle", "date", "time", "party_size", "name",
                   "phone", "email", "confirm", "committing", "done"]
    date: date | None = None
    time: time | None = None
    party_size: int | None = None
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    offered_alternatives: list[str] = []
    attempts: dict[str, int] = {}          # per-field retry counter
```

Persisted on `calls/{callSid}.slotState` after every change, so a 280-second cutover resumes mid-booking.

### 8.2 The sequence

```
idle ──"can I book a table"──► date
date       ask "what day?"          → parse; vague ("this weekend") → re-ask once
time       ask "what time?"         → parse; vague ("evening") → offer two real times
party_size ask "how many people?"   → int 1..MAX_PARTY (thresholds.yaml)
                                      over MAX_PARTY → CALLBACK, large group
           ▼
           check_availability(date, time, party_size)     ← §10
           full → speak 2–3 real alternatives, caller picks, re-check
           open → continue
           ▼
name       ask "what name is it under?"
phone      DEFAULT: use the caller ID from the webhook `From` parameter and
           confirm it — "is the number you're calling from the best one?"
           Only ask for a number if they say no. Never make a caller recite
           the number they are calling from.
email      §9
confirm    read back the whole booking in one sentence, ask for a yes
committing Firestore transaction (§8.3). Speak a filler first — the write
           takes 100–300 ms and silence at this moment reads as a dropped call.
done       "You're booked. A confirmation email is on its way to you."
           Then: send email, mirror to Calendar, both AFTER the commit.
```

Every utterance in this sequence is a **template** in `templates.py`. No model generates them. They are the same every time, which is exactly what you want from a receptionist taking a booking.

Per-field attempts are capped at `MAX_FIELD_ATTEMPTS` (default 2). Exceeding it on any field except email routes to `CALLBACK`. Exceeding it on email is handled in §9 and does **not** lose the booking.

### 8.3 The commit — concurrency-safe

Firestore transactions use optimistic concurrency: they re-run if a **document they read** changed before commit. They do **not** protect against a new document appearing that a query would have matched. So summing `partySize` over a query of `bookings` inside a transaction is **unsafe** — two concurrent callers each read a query lacking the other, both pass the capacity check, both commit.

The seat count is therefore a denormalised counter field on the slot document, and the transaction reads *that document*.

```python
@firestore.transactional
def _reserve(tx, slot_ref, booking_ref, req):
    slot = slot_ref.get(transaction=tx).to_dict()
    if slot["seatsBooked"] + req.party_size > slot["seatsTotal"]:
        raise SlotFull(alternatives=nearby_options(req))
    if booking_ref.get(transaction=tx).exists:        # Rule 5, idempotent retry
        return "already_exists"
    tx.update(slot_ref, {"seatsBooked": slot["seatsBooked"] + req.party_size})
    tx.create(booking_ref, booking_doc(req))
    return "created"
```

- **Do not use `FieldValue.increment()` here.** Increment is atomic but blind — it cannot refuse. The check and the write must sit in one transaction, which requires reading the current value.
- `booking_ref` ID is the idempotency key: `sha256(callSid + slotId + name + partySize)`, truncated.
- Calendar mirror and confirmation email are enqueued **after** the transaction commits, and both are idempotent. A Calendar failure must not fail the booking — a null `calendarSyncedAt` means "not mirrored yet" and the daily cron retries it.
- A cancellation decrements the counter in its own transaction.

`capacitySlots` documents must **exist** before a booking can lock one. `scripts/seed_business.py` materialises them from `config/capacity.yaml` for 60 days ahead; a missing slot is a booking failure, not an empty room.

### 8.4 Alternatives

When a slot is full, in `services/booking/capacity.py`: same day ±30 min, then same day ±60 min, then the next open day at the same time. Return at most three, only ones that genuinely have seats, phrased as real times — "seven, or quarter past eight tonight, or seven tomorrow." Never offer a time outside the windows in `config/capacity.yaml`.

---

## 9. Email capture

Designed to the owner's spec: normalise hard, score confidence, and escalate to **spelling the local part only** when unsure — never the whole address, because the domain is the predictable half and spelling it out wastes the caller's patience.

### 9.1 Normalisation

Applied in order, in `services/voice/email_capture.py`, on the raw STT text:

```
lowercase
"at" / "at the rate of" / " at sign "        → @
"dot" / "point" / "full stop"                → .
"underscore" / "under score"                 → _
"dash" / "hyphen" / "minus"                  → -
"plus"                                       → +
spelled digits ("zero".."nine")              → 0..9
strip all whitespace
collapse repeated @ and . to one
```

Then split on the last `@` into `local` and `domain`.

### 9.2 Confidence scoring

```python
class EmailCandidate(BaseModel):
    raw: str
    local: str
    domain: str
    domain_score: float
    local_score: float
    confidence: float          # min(domain_score, local_score)
    needs_spelling: bool
```

**`domain_score`** — fuzzy-match `domain` against `config/au_email_domains.yaml` (gmail.com, outlook.com, hotmail.com, yahoo.com.au, yahoo.com, icloud.com, me.com, bigpond.com, optusnet.com.au, iinet.net.au, tpg.com.au, live.com.au):

| Condition | Score |
| --- | --- |
| exact match | 1.0 |
| Levenshtein ≤ 2 of a listed domain | 0.85, and **correct it** ("gmail dot con" → gmail.com) |
| not listed but shape-valid (`x.tld`, tld 2–6 alpha) | 0.55 |
| shape-invalid or empty | 0.0 |

**`local_score`** — start at 1.0 and subtract:

| Condition | Penalty |
| --- | --- |
| length < 3 | 0.4 |
| length > 30 | 0.2 |
| contains a character outside `[a-z0-9._+-]` | 0.5 |
| 4 or more consecutive consonants | 0.25 |
| contains a space-derived artefact (a `.` adjacent to another `.`) | 0.3 |
| the STT provider returned a per-word confidence below 0.7 for this span | 0.3 |

If the STT provider exposes per-word confidence, multiply the final `local_score` by it; if it does not, rely on the heuristics alone. Write the function so both paths work — do not make the design depend on a provider feature you have not verified.

`confidence = min(domain_score, local_score)`.

### 9.3 The flow

```
confidence ≥ EMAIL_CONFIDENCE_THRESHOLD   (default 0.80, thresholds.yaml)
   → read it back once:
     "Let me check I've got that — sarah dot chen at gmail dot com?"
     · clear yes  → accept, stage=confirm
     · no / unclear → go to spelling below

confidence < threshold
   → "I want to get this right — can you spell the part before the at symbol
      for me, one letter at a time?"
   · capture letters; for confusable letters ONLY (b p d t e, m n, s f),
     confirm phonetically as they come: "B for bravo?"
   · rebuild local, keep the fuzzy-matched domain
   · recompute confidence, then read back once as above
```

Two spelling attempts maximum (`MAX_EMAIL_SPELL_ATTEMPTS`). After that:

**The booking still completes.** Set `email = null`, `emailCaptureFailed = true` on the booking, say *"I'll have the owner follow up to confirm by email"* rather than claiming an email was sent, and surface it on the dashboard as a flag on the booking. Never claim a confirmation was sent when it was not — and never lose a booking over an email address.

Log every normalisation and every correction to the turn document so the domain list and the penalties can be tuned from real calls rather than guessed.

### 9.4 What the caller hears when it works

> "Great — a table for four this Saturday at seven, under Sarah. Let me check I've got your email: sarah dot chen at gmail dot com?"
> — "Yep."
> "You're booked. A confirmation email is on its way to you. See you Saturday."
---

## 10. Tools

Five lookups, all in `services/voice/tools.py`. These are **Python functions called by the state machine and the answer path** — they are not exposed to Gemini, and the Groq router does not select them (it emits an intent; Python maps intent to lookup).

| Function | Reads | Returns | Grounding rule |
| --- | --- | --- | --- |
| `get_section_items(section)` | `menuItems` where `confirmedByOwner == true` | ≤ 3 items + a remaining count | Rule 7 |
| `lookup_menu_item(name)` | same, fuzzy name match | item + price + dietary tags | `priceCents == null` → unknown → callback |
| `get_item_allergens(name, allergen=None)` | the `allergens` map on the item document | status per allergen | Only entries with `confirmed == true`. Anything else → `unknown` → callback. Python appends `CROSS_CONTACT` (Rule 2) |
| `get_business_fact(fact_key)` | `facts/{factKey}` — one document get | the value | A missing document means unknown → callback (V9) |
| `check_availability(date, time, party_size)` | `capacitySlots` documents | open, or up to 3 alternatives | Reads the counter, never Calendar |

Plus two writes, called only by the state machines:

| Function | Effect |
| --- | --- |
| `commit_booking(slot_state, call_sid)` | §8.3 transaction, then enqueue email + Calendar mirror |
| `take_callback(name, phone, question, reason)` | Writes `callbacks/{auto}`, emails the owner immediately, appears on the dashboard as an open task. `reason` ∈ `allergen_unknown \| no_data \| complaint \| large_group \| catering \| other` |

The allergen map lives **on the item document** (not a subcollection) so one read gets an item and all its allergen states — matters for both turn latency and the 50k/day read quota.

Business context is loaded **once per call** at stream start into the session object: a name→item index, the confirmed fact keys, and the service windows. Not per turn.

---

## 11. Firestore writes

Reuse the existing collections. Add nothing new except `sessionType`.

| When | Write |
| --- | --- |
| `start` (new call) | `calls/{callSid}` — `startedAt`, `sessionType: "phone"`, `from`, `businessId`, `disclosureDone: false` |
| `start` (resume) | update `resumeCount`, `resumedAt` |
| per final turn | `calls/{callSid}/turns/{turnIndex}` — `speaker`, `textFinal`, `intent`, `routerConfidence`, `toolCalled`, `answerSource` (`menu` \| `fact` \| `allergen` \| `not_found` \| `template`), `sttMs`, `routerMs`, `answerMs`, `ttsMs`, `interrupted`, `usedFallback` |
| slot change | `calls/{callSid}.slotState` |
| 280 s cutover | `calls/{callSid}` — `historySummary`, `slotState`, `emailCandidate` |
| booking | `bookings/{idempotencyKey}` + `capacitySlots` counter, one transaction |
| callback | `callbacks/{auto}` |
| every provider call | `usageEvents/{auto}` — `provider`, `task`, `model`, `units`, `unitKind`, `costCents` |
| email sent | `emailsSent/{idempotencyKey}` |
| call end | `calls/{callSid}` — `endedAt`, `durationMs`, `outcome`, stage totals, `costCents`, `hourLocal`, `weekdayLocal` (denormalised for rollups) + one transaction incrementing `rollups/{localDate}` |

**Never written:** partial transcripts. One three-minute call at ten partials a second would be ~1,800 writes against a 20k/day quota, for text that is stale a second later. Partials go to the simulator HUD and the log stream only.

`hourLocal` and `weekdayLocal` are computed at write time in `Australia/Melbourne` so the nightly rollup never does timezone maths across documents.

---

## 12. The test simulator

This is what stops you placing a phone call every time you change a prompt. **It must drive the production pipeline, not a copy of it** (R7) — a simulator that tests a different code path is worse than no simulator, because it gives you false confidence.

### 12.1 Two modes

**Mode A — text harness.** The one you run a hundred times a day.

```
POST /sim/text  {"text": "do the toasties have nuts?"}
  → skips VAD, Smart Turn, STT and TTS entirely
  → runs Groq router → intent → tool lookup → Gemini or template
  → returns: intent, confidence, extracted field, tool called, tool result,
             slot state before/after, the exact text that would go to TTS,
             and per-stage ms
```

Instant, free, and scriptable. `tests/conversations/*.yaml` run through this path in CI.

**Mode B — browser audio simulator.** For testing transcription, VAD and turn-taking.

A page at `/sim` (localhost only) with a Call button that captures mic audio and — this is the critical part — **encodes it to 8 kHz μ-law and wraps each frame in the exact Twilio JSON envelope** before sending it over the WebSocket:

```json
{"event":"media","sequenceNumber":"3","streamSid":"MZsim...",
 "media":{"track":"inbound","chunk":"1","timestamp":"20","payload":"<base64 μ-law>"}}
```

It sends a synthetic `connected` then `start` message first, with `streamSid` prefixed `MZsim` and a valid simulator token, and handles `media`, `mark` and `clear` coming back the same way Twilio would. `api/voice/ws.py` therefore needs **no simulator-specific branch** — it cannot tell the difference, which is the whole point.

A **telephony-fidelity toggle** on the page, default ON: downsample the mic to 8 kHz μ-law and back. With it off you are testing 16 kHz audio that production never sees, and you will pass tests the phone then fails. Leave it on unless you are deliberately measuring the codec's contribution.

### 12.2 What the simulator page shows

Left: a transcript pane with partials rendering live and finals locking in.
Right, per turn: intent + confidence badge, the field extracted, which tool fired with its arguments, the tool's raw result, whether Gemini or a template produced the reply, and a stage-timing bar against the §4.1 budget.
Bottom: current slot state as a table, current email candidate with its three scores, and a warning strip that lights up on a router parse failure, a Gemini fallback, or a missing composite index.

That last one matters: the fastest way to find a Firestore index you forgot to declare is to see the error in the simulator rather than hear silence on a phone call.

### 12.3 Isolation — structural, not filtered (V12)

```python
# services/voice/sinks.py
class CallSink(Protocol):
    def write_call(...): ...
    def write_turn(...): ...
    def write_booking(...): ...

class FirestoreSink:   # production
class LocalJsonlSink:  # simulator — appends to ./.testruns/{callId}.jsonl
```

The sink is chosen at session creation from `SESSION_SINK`, which the simulator sets to `local` unconditionally. **`LocalJsonlSink` holds no Firestore client at all**, so a simulator run physically cannot write to the dashboard's data — it is not a `where` clause someone can forget.

Reads are different: the simulator must read the *real* menu and facts or it tests nothing. So reads go to Firestore (or the emulator) normally; only writes are diverted.

`tests/test_sim_isolation.py` asserts this by instantiating a simulator session and confirming that no Firestore write method is reachable from it. Make that test hard to delete.

Two exceptions worth allowing, both off by default:
- `SESSION_SINK=emulator` — writes to the Firebase emulator, for testing the dashboard's rendering against synthetic calls.
- Bookings made in simulator mode never write to `capacitySlots`, so a test run cannot consume real seats.

Add `.testruns/` to `.gitignore`.

### 12.4 Replay

`scripts/replay.py .testruns/{callId}.jsonl` re-runs a recorded call's caller turns through Mode A. This is how a regression found on Sunday gets reproduced on Monday. Without it, every bug is a bug you have to re-provoke by talking to your laptop.

---

## 13. Build order and acceptance tests

Five phases. Do not start one until the previous phase's tests pass (R1). Ordered so that stopping early still leaves something demonstrable.

### Phase 1 — Twilio plumbing, no intelligence

Build: `api/voice/incoming.py`, `ws.py`, `status.py`, `fallback.py`; `services/voice/twiml.py`, `signature.py`, `audio.py`, `stream.py`; the pre-rendered greeting.

Done when:
- A real call to the number plays the disclosure greeting and then echoes the caller's own audio back to them.
- `test_signature.py` passes, including the `PUBLIC_BASE_URL` reconstruction case.
- `test_audio.py` round-trips a μ-law fixture through 8k→16k→8k with no exception on your target Python version.
- An unsigned POST to `/api/voice/incoming` returns 403. A WebSocket connection without a valid token is closed.
- `vercel.json` shows `syd1` in the function logs.

### Phase 2 — The simulator

Build: `api/sim/app.py`, the `/sim` page, both modes, `sinks.py`.

Done when:
- Mode B produces byte-identical Twilio envelopes to a captured real call (compare against a recording from Phase 1).
- `api/voice/ws.py` contains **zero** simulator-specific branches.
- `test_sim_isolation.py` passes.
- Mode A answers a typed turn end to end in under a second.

Do this before the intelligence. Everything after is ten times faster to build with it in place.

### Phase 3 — Routing and grounded answering

Build: `router.py`, `answerer.py`, `tools.py`, `templates.py`, `telemetry.py`.

Done when these conversation suites pass through Mode A:

| Suite | Asserts |
| --- | --- |
| `disclosure.yaml` | First utterance contains the AI + recording + third-party line |
| `menu_question.yaml` | Answers from a confirmed item; the price spoken matches Firestore exactly |
| `menu_recitation.yaml` | Never returns more than three items (Rule 7) |
| `price_unknown.yaml` | `priceCents == null` → escalates, never guesses |
| `allergen_confirmed.yaml` | Answers, and `CROSS_CONTACT` is present verbatim |
| `allergen_unknown.yaml` | Escalates with `reason='allergen_unknown'`, and **never names an allergen status** |
| `fact_missing.yaml` | A missing `facts/` document → callback, no invention |
| `router_garbage.yaml` | Two unparseable Groq responses → `CALLBACK`, call still alive |
| `gemini_timeout.yaml` | Gemini stubbed to time out → template fallback speaks the real fact |
| `chitchat.yaml` | One short line, no facts asserted |

### Phase 4 — Booking and email

Build: `booking_machine.py`, `email_capture.py`, the commit transaction, Calendar mirror, confirmation email.

Done when:
- `test_booking_concurrency.py`: 20 concurrent requests for 4 remaining seats produce exactly the bookings that fit, zero overbooking. **Run it 50 times** — optimistic transactions retry, so one green run is not evidence.
- `test_booking_machine.py`: vague date and vague time each re-ask once; over-`MAX_PARTY` routes to callback; a full slot offers 2–3 real alternatives that all have seats; caller ID is offered as the phone number rather than asked for.
- `test_email_capture.py`: "sarah dot chen at gmail dot con" → `sarah.chen@gmail.com` with `domain_score` 0.85; a 2-character local part drops below threshold and triggers spelling; two failed spelling attempts still produce a booking with `emailCaptureFailed = true` and the agent does **not** claim an email was sent.
- A real call books a table, the event appears in the owner's Google Calendar, and the email arrives.
- Killing the Calendar credentials does not fail the booking.

### Phase 5 — Resume, telemetry, hardening

Build: the 280 s cutover, rollup increments, cost accounting, status callbacks.

Done when:
- A real call held past 280 seconds continues, mid-booking state intact, with one acknowledging line and no lost slots.
- `resumeCount > 3` ends the call gracefully with a callback instead of looping.
- The disclosure plays exactly once across a cutover.
- Per-turn stage timings are on every turn document; `rollups/{date}` increments at call end.
- A hangup mid-turn sets `outcome='abandoned'` from the status callback, not from a guess.

---

## 14. Environment variables

`services/common/config.py` parses these at import and **raises on a missing required var** rather than failing at 2am on a live call. Add to `.env.example` with no values.

| Var | Required | Default | Note |
| --- | --- | --- | --- |
| `PUBLIC_BASE_URL` | yes | — | The exact public origin Twilio calls. §6.2 depends on it |
| `TWILIO_ACCOUNT_SID` | yes | — | |
| `TWILIO_AUTH_TOKEN` | yes | — | Signature validation. Server only |
| `TWILIO_PHONE_NUMBER` | yes | — | The AU mobile number |
| `WS_TOKEN_SECRET` | yes | — | §6.3 HMAC secret |
| `GROQ_API_KEY` | yes | — | |
| `GROQ_ROUTER_MODEL` | no | `llama-3.1-8b-instant` | |
| `GEMINI_API_KEY` | yes | — | Free tier (V4) |
| `ELEVENLABS_API_KEY` | yes | — | |
| `ELEVENLABS_VOICE_ID` | yes | — | |
| `ELEVENLABS_TTS_MODEL` | no | `eleven_flash_v2_5` | `output_format=ulaw_8000` is set in code, not here |
| `BUSINESS_ID` | yes | — | Single tenant; still on every document |
| `FIREBASE_PROJECT_ID` | yes | — | |
| `FIREBASE_SA_JSON` | yes | — | base64. Server only |
| `FIRESTORE_EMULATOR_HOST` | no | — | Dev/test only. **Must not be set in Vercel** |
| `GOOGLE_CALENDAR_ID` | yes | — | Owner's calendar, shared to the service account |
| `GOOGLE_SA_JSON` | yes | — | base64 |
| `EMAIL_PROVIDER` | no | `gmail` | `gmail` \| `resend` |
| `GMAIL_USER` / `GMAIL_APP_PASSWORD` | if gmail | — | |
| `RESEND_API_KEY` | if resend | — | |
| `SESSION_SINK` | no | `firestore` | `firestore` \| `local` \| `emulator` (§12.3) |
| `ENABLE_SIM` | no | `0` | `/sim` refuses to mount unless `1` |
| `TZ_BUSINESS` | no | `Australia/Melbourne` | |

Every tunable number lives in `config/thresholds.yaml`, not scattered as literals: `ROUTER_MIN_CONFIDENCE` 0.6, `EMAIL_CONFIDENCE_THRESHOLD` 0.80, `MAX_EMAIL_SPELL_ATTEMPTS` 2, `MAX_FIELD_ATTEMPTS` 2, `MAX_PARTY` 12, `LARGE_GROUP_THRESHOLD` 10, `MAX_REPLY_WORDS` 40, `GEMINI_TIMEOUT_MS` 1200, `GROQ_TIMEOUT_MS` 800, `STREAM_CUTOVER_MS` 280000, `CUTOVER_WARN_MS` 240000, `MAX_RESUMES` 3.

---

## 15. Setup checklist

Put this in `docs/setup-checklist.md`. These are the things that fail at the worst moment.

**Google Calendar — most likely to be silently broken**
1. Create a service account, base64 its JSON into `GOOGLE_SA_JSON`.
2. **In the owner's Google Calendar, share the calendar with the service account's email address and grant "Make changes to events."** A service account gets its own empty calendar the owner cannot see. Without this step bookings land nowhere and nothing errors. Domain-wide delegation is the usual fix and needs Google Workspace, which an independent toastie shop does not have.
3. Verify by looking at the owner's own calendar UI, not an API response.

**Twilio**
4. Upgrade off the trial account — a trial plays Twilio's own message before your greeting and only accepts verified callers.
5. Number's "A call comes in" → `POST {PUBLIC_BASE_URL}/api/voice/incoming`; "Primary handler fails" → `/api/voice/fallback`.
6. Buy the AU mobile number early; AU KYC is tightening.
7. For local testing: start the tunnel, then set `PUBLIC_BASE_URL` to the tunnel origin **before** starting the app, or signature validation fails on every request.

**Firebase**
8. Deploy every composite index before any demo. A missing one fails the query at runtime and the caller hears silence.
9. Confirm `FIRESTORE_EMULATOR_HOST` is not set in Vercel's environment.
10. `scripts/seed_business.py` has materialised `capacitySlots` for the next 60 days.

**Demo day**
11. `scripts/warm.sh` immediately before presenting — cold start lands on the first caller.
12. Place one real test call end to end, including the email arriving.
13. Check that at least one `rollups/` document exists so the dashboard is not empty on screen.

---

## 16. Known limitations — say these before a judge finds them

Put in `docs/known-limits.md` and in the pitch. A limitation you name reads as judgement; the same one discovered live reads as a bug.

1. **Calls are cut and re-streamed at 280 seconds** by Vercel Hobby's 300 s function ceiling. State survives; the caller hears a 1–2 second pause. Removed by a paid plan or a persistent host.
2. **8 kHz telephony audio** measurably degrades transcription of spelled strings and unusual item names. §9 exists because of it.
3. **Caller conversations are processed by free-tier Gemini**, which may use content to improve Google's products and may be human-reviewed. Disclosed in the greeting. Move to a paid tier (~half a cent per call) before ongoing real use.
4. **Allergens are refused unless the owner has confirmed them.** Deliberate. The refusal is the feature.
5. **Ordering and upselling are out of scope.**
6. **Capacity is seats per service window, not per table.** A 40-seat room cannot really seat ten parties of two at once.
7. **One vertical** — restaurant only.
8. **Email confirmation can fail silently** if the address is mis-transcribed past two spelling attempts. The booking survives and is flagged for owner follow-up; the agent does not claim an email was sent.
9. **No SMS, no outbound calls, no live human transfer.** Callbacks are the escalation path.
10. **Single tenant.** Every document carries `businessId` so this is configuration later, not a rewrite.

---

## 17. Start here

1. Read the existing `services/common/*` and the marketing agent to match conventions (R2).
2. Create `contracts/voice.py`, `config/thresholds.yaml` and `config/disclaimers.py` first — everything else references them.
3. Build Phase 1. Get a real call echoing your own voice back before writing a single line of model code.
4. Build Phase 2. From then on you are iterating in a browser, not on the phone.
5. Then phases 3, 4, 5 in order, with the gates.

When something is missing from this document, emit `TODO(spec):` and ask. Do not choose for me.