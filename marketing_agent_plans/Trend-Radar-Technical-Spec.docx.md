# **TREND RADAR — TECHNICAL IMPLEMENTATION SPEC**

**Audience:** Claude (or any implementation agent). Treat this document as the source of truth. If a later chat message conflicts with this spec, this spec wins unless the user explicitly overrides a numbered constraint.

**Classification:** Engineering design \+ build contract  

**Status:** v1.0 — approved product decisions locked  

**Stack:** Python 3.11+, Google Cloud Firestore (shared Firebase project), Gemini free tier, ScrapeCreators, parent-hosted dashboard  

**Reference formula only:** D:\\Editoz Club\\Acceleratoz-aios-feat-portable-install\\tools\\idea-scout\\score-ideas.py  

**Do not clone:** Editoz AIOS video factory (Higgsfield, Remotion, Telegram, Claude Code, publishing)

————————————————————————————

## **0\. How to use this document**

You are implementing a **library**, not a product company.

1. Read §1–§3 before writing any file.  
2. Implement in the order in §16. Do not skip the Firebase namespace and cache layer.  
3. Numbers are deterministic Python. Gemini never invents metrics, never picks scrape endpoints, never decides credit spend.  
4. If a path, key, or field is unspecified, add it **under** users/{userId}/marketingRadar/ and document it. Do not invent top-level Firestore collections.  
5. If you cannot find the parent questionnaire path, implement CONTEXT\_PATH as an env override. Default: users/{userId}/context.  
6. Do not open a new FastAPI process, do not create a new Firebase project, do not create a new questionnaire, do not add Anthropic.

————————————————————————————

## **1\. Problem and system context**

The parent product is an agentic dashboard with an **overlord** (main agent) and a **Marketing** section. The user already completed a short questionnaire at first run. That result lives in the **shared Firebase** (likely a Firestore doc or a Storage object named context.md).

Trend Radar is the marketing **sub-agent**. It:

* Reads that context once per calendar day and caches it locally.  
* Every **2 days**, pulls public social data (TikTok \+ Instagram always; Facebook page/group or YouTube Shorts as the third paid call).  
* Scores posts with a port of the AIOS score-ideas.py algorithm (relative velocity, no Higgsfield term).  
* Calls Gemini **once** per paid scan to write a ScanBrief (global trends, niche trends, film-this, playbook).  
* Lets the user Like a card → 3 angles \+ filming guide \+ script. Saved scripts persist. Unsaved drafts expire at 14 days.  
* Exposes a Gemini chat **inside the Marketing section**.  
* Writes usage \+ credit alerts so the Marketing UI and the overlord can show remaining credits without live-scraping.

The overlord must be able to answer “what’s trending / what should I film / how many credits” by **reading** the latest packet and usage snapshot. The overlord must **not** scrape, must **not** write marketingRadar/\*\*, and must **not** call Gemini to regenerate a brief.

————————————————————————————

## **2\. Hard constraints (do not violate)**

7. **Not a standalone app.** Ship as an importable package marketing\_radar started by the parent process. No python app.py server of our own. No first-visit questionnaire UI.  
8. **Shared Firebase only.** Same project as the overlord. Writes exclusively under users/{userId}/marketingRadar/\*\*. Read-only on users/{userId}/context (or CONTEXT\_PATH).  
9. **No Supabase. No SQLite as source of truth.** Local disk is a cache, not the database.  
10. **All AI \= Gemini free tier.** One GEMINI\_API\_KEY. No ANTHROPIC\_API\_KEY. No Gemini Pro. Do not enable paid Gemini billing from this module.  
11. **ScrapeCreators budget:** 100 free credits, never expire, 1 credit ≈ 1 HTTP request, cached identical request within 48h \= 0 credits. Max **3 live** paid calls per scan. **15-credit reserve** for Like-transcripts. Never paginate. Never comments. Never audience-demographics. Never TikTok use\_ai\_as\_fallback (10 credits).  
12. **Do not call** GET /v1/account/credit-balance on dashboard refresh. It may cost 1 credit. Use credits\_remaining on the last paid response. Call balance only if we have no cached figure **and** a scan is about to run.  
13. **Firebase reads for agent/UI:** at most **one pull per calendar day** of context \+ latest ScanBrief \+ usage snapshot. Split into local cache. Re-fetch only if cache missing, older than 24h, or explicitly invalidated after a write we just made (scan, like, save).  
14. **Cross-platform:** pathlib only. No hardcoded /Users/... or C:\\. APScheduler inside the parent process. No launchd, no Windows Task Scheduler, no bash install.sh.  
15. **Auth:** use the parent’s userId. Do not build a second auth system.  
16. **Out of scope forever for v1:** video generation, publishing, Telegram, Claude Code, Remotion, Higgsfield, Facebook “global trending”, writing back into the main context doc.

————————————————————————————

## **3\. Runtime architecture**

Parent process  
 ├─ Overlord agent          READ marketingRadar/latest, scans/{id}, usage/snapshot  
 ├─ Marketing section UI    board \+ chat \+ Like/Save  (parent-hosted)  
 └─ marketing\_radar package  
      ├─ APScheduler  
      │    daily\_pull   00:05 local (or first request of the day)  
      │    scan         every 48h from last successful paid scan  
      │    expire\_drafts daily  
      ├─ db.firebase    namespaced client  
      ├─ cache.local    split ScanBrief \+ context  
      ├─ scrapers       allowlist \+ free adapters  
      ├─ scoring        normalize \+ AIOS relative score  
      └─ agent.gemini   ladder \+ synthesize / recap / chat / angles / script

**Write path (marketing agent only):** scrape → normalize → score → Gemini synthesis → Firestore ScanBrief \+ posts \+ latest \+ usage.

**Read path (UI / chat / overlord):** local cache. If miss → Firestore → re-cache. Never ScrapeCreators on read.

**In-process invalidation:** after a scan, Like, save, or chat tool that mutates data, update Firestore **and** rewrite the local cache immediately. That does not count as a “daily pull”; it is a write-through.

————————————————————————————

## **4\. Process and package layout**

Place the package where the parent repo already lives. If the parent is not in this workspace yet, scaffold at:

D:\\Editoz Club\\marketing\_radar\\

marketing\_radar/  
  \_\_init\_\_.py  
  config.py                 \# env, ladder caps, reserve=15, scan\_interval\_hours=48  
  scheduler.py              \# register APScheduler jobs on parent startup  
  jobs/  
    daily\_pull.py           \# Firestore → local cache  
    scan.py                 \# paid scan \+ off-day recap  
    expire\_drafts.py  
  db/  
    firebase.py             \# Admin SDK or google-cloud-firestore  
    paths.py                \# single source of path builders  
  cache/  
    local.py                \# disk JSON under user-writable dir (platformdirs or Path.home())  
  context/  
    parse.py                \# context.md / Firestore map → ContextProfile  
  scrapers/  
    client.py               \# ScrapeCreators allowlist \+ hash cache  
    endpoints.py            \# frozen allowlist  
    free\_youtube.py  
    free\_reddit.py  
    free\_trends.py  
  scoring/  
    normalize.py  
    score.py                \# port of score-ideas.py \--relative \+ niche\_fit \+ platform\_weight  
    niche\_fit.py  
  packets/  
    schema.py               \# pydantic v2 models  
  usage/  
    snapshot.py  
    alerts.py  
  agent/  
    gemini.py               \# ladder  
    prompts.py  
    chat.py  
    like.py  
  services/  
    brief.py                \# get\_brief / get\_post for UI and overlord  
    stats.py  
    scripts.py

Parent wires:

from marketing\_radar.scheduler import start\_marketing\_radar  
start\_marketing\_radar(app\_or\_loop, user\_id=...)

Parent may mount these handlers on **its** app (do not start port 8000 ourselves):

* GET  /api/brief  
* GET  /api/brief/{scan\_id}  
* GET  /api/posts/{post\_id}  
* GET  /api/stats  
* POST /api/chat  
* POST /api/posts/{post\_id}/like  
* POST /api/scripts/{script\_id}/save  
* GET  /api/scripts?status=saved

If the parent prefers direct Firestore from the browser, still implement the Python functions; the JSON shapes below are the contract.

————————————————————————————

## **5\. Environment**

All keys live in the **parent** .env. Never commit them.

FIREBASE\_PROJECT\_ID=  
FIREBASE\_CREDENTIALS\_JSON=          \# path to service account, or ADC  
CONTEXT\_PATH=users/{userId}/context \# override if main app uses Storage or another doc  
SCRAPECREATORS\_API\_KEY=  
GEMINI\_API\_KEY=  
YOUTUBE\_API\_KEY=                    \# optional; skip free YT Data if missing  
GEMINI\_LADDER\_JSON=                 \# optional override of model ids \+ daily\_cap  
MARKETING\_USER\_ID=                  \# v1 single-user; parent injects real uid

Service account IAM:

* Cloud Firestore: read users/{uid}/context  
* Cloud Firestore: read/write users/{uid}/marketingRadar/\*\*  
* **Deny write** everywhere else

If context is Cloud Storage (context.md), grant storage.objects.get on that object only.

————————————————————————————

## **6\. Firestore namespace (collision avoidance)**

**Never create** top-level collections named profiles, scans, posts, scripts, notifications, users as \*our\* product collections. Nest under the existing user document if the parent already uses users/{userId}.

### **6.1 Path map**

| Purpose | Path | Writer | Reader |
| :---- | :---- | :---- | :---- |
| Questionnaire / context.md | users/{uid}/context | Parent only | Marketing (daily), never write |
| Parsed context cache (remote copy) | users/{uid}/marketingRadar/contextCache | Marketing | Marketing |
| Pointer | users/{uid}/marketingRadar/latest | Marketing | All |
| ScanBrief packet | users/{uid}/marketingRadar/scans/{scanId} | Marketing | All |
| TrendPacket | users/{uid}/marketingRadar/posts/{postId} | Marketing | All |
| Script | users/{uid}/marketingRadar/scripts/{scriptId} | Marketing | UI, overlord (saved) |
| Usage snapshot | users/{uid}/marketingRadar/usage/snapshot | Marketing | All |
| Usage event | users/{uid}/marketingRadar/usage/events/{eventId} | Marketing | Marketing |
| Gemini day counters | users/{uid}/marketingRadar/usage/geminiDaily/{yyyy-mm-dd-PT} | Marketing | Marketing |
| Raw scrape | users/{uid}/marketingRadar/scrapeCache/{requestHash} | Marketing | Marketing |
| Playbook lesson | users/{uid}/marketingRadar/playbook/{entryId} | Marketing | Marketing, Gemini prompt |
| Alert | users/{uid}/marketingRadar/notifications/{alertId} | Marketing | UI, overlord |
| Chat message | users/{uid}/marketingRadar/chat/{threadId}/messages/{msgId} | Marketing | Marketing UI |

Implementation note: Firestore is a document store. usage/snapshot means collection usage with document id snapshot under the marketingRadar document, **or** a subcollection. Pick one layout and keep paths.py as the only place that encodes it.

Recommended concrete layout (subcollections under a marker doc):

users/{uid}                                      \# existing parent user doc  
  fields: ...parent data...  
  collection: marketingRadar  
    doc: meta  
      fields: { initialized: true }  
    collection: docs  
      doc: contextCache  
      doc: latest  
    collection: scans  
      doc: {scanId}  
    collection: posts  
      doc: {postId}  
    collection: scripts  
      doc: {scriptId}  
    collection: usage  
      doc: snapshot  
      collection: events / doc {eventId}  
      collection: geminiDaily / doc {pacificDate}  
    collection: scrapeCache  
      doc: {requestHash}  
    collection: playbook  
      doc: {entryId}  
    collection: notifications  
      doc: {alertId}  
    collection: chat  
      doc: {threadId}  
        collection: messages / doc {msgId}

paths.py must hide this so a later parent-schema change is one file.

### **6.2 Security rules (hand to the parent Firebase owner)**

match /users/{uid}/context {  
  allow read: if request.auth.uid \== uid || marketingServiceAccount();  
  allow write: if parentAppOnly();  
}

match /users/{uid}/marketingRadar/{document=\*\*} {  
  allow read: if request.auth.uid \== uid || overlordServiceAccount() || marketingServiceAccount();  
  allow write: if marketingServiceAccount();  
}

Do not deploy rules that lock the parent out of their own users/{uid} fields.

### **6.3 Storage decision (locked)**

**Hybrid.**

* One **ScanBrief document per scan** (the packet). After daily pull, Python splits it into local cache keys: weekly\_take, global, niche, film\_this, playbook, profile, credits.  
* Sibling docs only for lifecycle: scripts, usage, scrapeCache, contextCache, chat, notifications.  
* **Never embed raw scrape JSON in the ScanBrief.** Firestore 1 MB/doc limit. Raw payloads go to scrapeCache and may be pruned after 14 days.

Do not store “the entire module” as one Firebase row.

————————————————————————————

## **7\. Context contract (no questionnaire)**

### **7.1 Inputs you must accept**

context/parse.py must accept **either**:

* A Firestore map, or  
* A markdown/text blob (context.md)

Extract, with safe defaults:

class ContextProfile:  
    user\_id: str  
    niche: str  
    keywords: list\[str\]          \# 3–6  
    hashtags: list\[str\]  
    platforms: list\[str\]         \# default \["tiktok", "instagram"\]  
    format: str | None  
    region: str | None  
    language: str | None  
    goal: str | None             \# followers | leads | authority  
    facebook\_page\_urls: list\[str\]  \# max 2  
    facebook\_group\_urls: list\[str\] \# max 1  
    competitor\_handles: list\[str\]  \# max 3  
    subreddits: list\[str\]        \# 0–2; may be Gemini-filled into contextCache only  
    audience\_line: str | None  
    raw\_text: str                \# original blob for debugging  
    source\_path: str  
    pulled\_at: datetime

If a field is missing, do not block the scan. Default platforms TikTok+Instagram. Empty Facebook URLs → Call 3 cannot be Facebook.

### **7.2 Optional expand (only if hashtags empty)**

One Gemini call, purpose=expand. Write result to marketingRadar/contextCache **only**. Never write the parent context document.

If Gemini is down: split niche \+ keywords on whitespace/commas. Continue.

### **7.3 Daily pull algorithm**

if local cache exists AND cache.pulled\_date \== today\_local AND cache.files\_present:  
    return cache  
fetch context, latest, scans/{latest.scanId}, usage/snapshot  
write local cache (atomic replace)  
return cache

If latest is missing (first run): cache context only; Marketing UI shows empty state “first scan scheduled”; do not scrape on page load.

————————————————————————————

## **8\. Local cache**

Directory (cross-platform):

Path(platformdirs.user\_cache\_dir("marketing\_radar")) / user\_id /

Files:

* context.json — ContextProfile  
* brief.json — full ScanBrief  
* brief\_parts/ — one JSON file per split key  
* stats.json — usage snapshot  
* meta.json — { pulled\_at, pulled\_date, scan\_id, schema\_version }

If any required file is missing, treat as cache miss and re-hydrate from Firestore. Do not partially serve a torn cache.

————————————————————————————

## **9\. Data contracts (Pydantic, schema\_version \= "1.0")**

### **9.1 ScanBrief — stored at \`scans/{scanId}\` and returned by \`get\_brief()\`**

{  
  "schema\_version": "1.0",  
  "scan\_id": "2026-09-12",  
  "generated\_at": "2026-09-12T02:00:00Z",  
  "next\_scan\_at": "2026-09-14T02:00:00Z",  
  "kind": "paid\_scan | offday\_recap",  
  "profile": { "niche": "...", "platforms": \["tiktok", "instagram"\], "goal": "followers" },  
  "credits": { "remaining": 82, "spent\_this\_scan": 3, "reserve": 15, "source": "scrapecreators" },  
  "usage": { "href": "/api/stats" },  
  "sources\_used": \["tiktok\_trending", "instagram\_hashtag", "youtube\_data\_api", "reddit", "google\_trends"\],  
  "weekly\_take": "...",  
  "patterns": \["...", "...", "..."\],  
  "ignore": \["...", "...", "..."\],  
  "global": \[ { "post\_id": "tt\_123", "packet\_ref": "posts/tt\_123" } \],  
  "niche": \[ { "post\_id": "ig\_456", "packet\_ref": "posts/ig\_456" } \],  
  "film\_this": { "post\_id": "tt\_123", "why": "..." },  
  "playbook\_delta": \["..."\],  
  "model\_used": "gemini-2.5-flash",  
  "quality": "medium"  
}

scan\_id format: YYYY-MM-DD of the scan’s UTC date. If two scans somehow share a date, suffix \-2. **Never overwrite** an existing scans/{scanId}; a new scan is a new document. Update latest to point at it.

### **9.2 TrendPacket — \`posts/{postId}\`**

{  
  "schema\_version": "1.0",  
  "platform": "tiktok | instagram | youtube | facebook | reddit",  
  "post\_id": "tt\_123",  
  "url": "https://...",  
  "author": "...",  
  "published\_at": "2026-09-11T18:00:00Z",  
  "likes": 0,  
  "comments": 0,  
  "shares": 0,  
  "views": null,  
  "duration\_sec": null,  
  "caption": "...",  
  "hook": null,  
  "hashtags": \[\],  
  "sound": null,  
  "velocity": 0.0,  
  "recency": 0.0,  
  "niche\_fit": 0.0,  
  "platform\_weight": 1.0,  
  "final": 0.0,  
  "why\_it\_works": null,  
  "format\_guess": null,  
  "liked": false,  
  "transcript": null,  
  "breakdown": null,  
  "angles": null,  
  "chosen\_angle": null,  
  "filming\_guide": null,  
  "script\_id": null  
}

post\_id \= {platform\_short}\_{provider\_id} so TikTok and Instagram cannot collide.

### **9.3 Script — \`scripts/{scriptId}\`**

{  
  "script\_id": "uuid",  
  "user\_id": "...",  
  "post\_id": "tt\_123",  
  "status": "draft | saved",  
  "angle": "...",  
  "filming\_guide": "...",  
  "script": "...",  
  "model\_used": "...",  
  "quality": "high | medium | low",  
  "created\_at": "...",  
  "saved\_at": null,  
  "expires\_at": "2026-09-26T02:00:00Z"  
}

On save: status=saved, saved\_at=now, expires\_at=null.  

Daily job deletes status==draft AND expires\_at \< now. Saved rows are never auto-deleted.

### **9.4 Usage snapshot — §12. Must match this shape exactly so the parent widgets do not fork.**

————————————————————————————

## **10\. ScrapeCreators client**

### **10.1 Allowlist (frozen)**

Scan endpoints (pick at most 3 live per paid scan):

| Use | Method \+ path |
| :---- | :---- |
| Global TikTok | GET /v1/tiktok/get-trending-feed |
| Global YouTube Shorts | GET /v1/youtube/shorts/trending |
| Global Instagram | GET /v1/instagram/reels/trending |
| Niche TikTok | GET /v1/tiktok/search/hashtag or GET /v1/tiktok/search/keyword |
| Niche YouTube | GET /v1/youtube/search |
| Niche Instagram | GET /v1/instagram/search/hashtag |
| Niche Facebook page | GET /v1/facebook/profile/reels |
| Alt Facebook group | GET /v1/facebook/group/posts |

Like-only (0 or 1 extra credit, skipped if remaining ≤ 15):

| Platform | Path | Notes |
| :---- | :---- | :---- |
| TikTok | GET /v1/tiktok/video/transcript | Never use\_ai\_as\_fallback |
| Instagram | GET /v2/instagram/media/transcript | duration ≤ 120s |
| YouTube | prefer youtube-transcript-api; fallback GET /v1/youtube/video/transcript |  |
| Facebook | GET /v1/facebook/post/transcript | duration ≤ 120s |

**Forbidden in v1:** comments, comment replies, audience demographics, photos, events, profile-search pagination, LinkedIn, X, Pinterest, TikTok live, any URL not in this table.

scrapers/client.py must raise if a caller passes a non-allowlisted path.

### **10.2 Request rules**

* Header: x-api-key: $SCRAPECREATORS\_API\_KEY  
* request\_hash \= sha256(method \+ url \+ sorted(params))  
* If scrapeCache/{hash} exists and expires\_at \> now: return cached JSON, credits\_charged \= 0, do not increment spend.  
* Else: HTTP GET, store raw JSON \+ expires\_at \= now+48h, read credits\_remaining / credits\_charged from the body, append usage/events.  
* No pagination parameters. First page only.  
* Timeouts: 30s. One retry on 502/503 only. Never retry a 200\. Never retry a 402/429 as a new spend without logging.

### **10.3 Scan cadence (paid day)**

Always:

* Call 1 \= TikTok  
* Call 2 \= Instagram  
* Call 3 \= secondary, **alternating** Facebook page/group **or** YouTube Shorts trending

Rotation table (scan\_index starts at 0, increment only after a successful paid scan):

| scan\_index % 4 | Call 1 | Call 2 | Call 3 |
| :---- | :---- | :---- | :---- |
| 0 | TikTok trending | Instagram hashtag (niche) | Facebook page reels if a page URL exists, else YT Shorts trending |
| 1 | TikTok hashtag (niche) | Instagram trending reels | YT Shorts trending; if free YT Data already covered Shorts, extra TT/IG keyword instead |
| 2 | TikTok keyword (niche) | Instagram hashtag (niche) | Facebook group if set, else Facebook page, else YT Shorts |
| 3 | TikTok trending | Instagram trending reels | YouTube Shorts trending |

Credit shedding:

* remaining ≤ 15: drop Call 3 (TT+IG only).  
* remaining \== 1: TikTok only.  
* remaining \== 0: skip paid scrape; write an exhausted alert; still run free APIs \+ recap if a prior packet exists.

Free APIs **always** on scan days (0 ScrapeCreators credits):

* YouTube Data API v3 (if key present; track units vs 10\_000/day, reset midnight Pacific)  
* Reddit /r/{sub}/hot.json (1–2 subs from context)  
* Google Trends via pytrends for context keywords (global vs niche)

### **10.4 Off-day (the 24h between paid scans)**

* 0 ScrapeCreators calls  
* Rescore existing cached posts (timestamps moved)  
* Refresh Trends \+ Reddit \+ free YouTube if quota remains  
* **One** Gemini recap, purpose=recap: “nothing new spent — here’s what still holds” \+ playbook refresh  
* Write a new ScanBrief with kind=offday\_recap (new scan\_id, e.g. {date}-recap). Still a new document. Update latest.

————————————————————————————

## **11\. Scoring (port AIOS, do not import Higgsfield)**

Copy the math from score-ideas.py. Differences that are **required**:

* Do **not** use higgsfield\_producibility.  
* Always apply **relative** velocity (the \--relative path).  
* Multiply by niche\_fit and platform\_weight.

### **11.1 Per-post**

hours \= max(0.5, seconds\_since\_published / 3600\)   \# unknown published\_at → hours \= None  
weighted \= likes \+ 3\*comments \+ 5\*shares           \# shares None → 0  
raw \= 0 if hours is None else weighted / hours

recency \= 0.5 if published\_at unknown else max(0.3, 1 \- days/14)

niche\_fit ∈ \[0, 1\]  \# token overlap of caption+hashtags vs context.keywords+hashtags+niche  
                    \# 0.0 if no text; 1.0 if any exact hashtag match; else Jaccard-ish overlap

platform\_weight \= 1.0 if platform in {tiktok, instagram} else 0.85

Facebook view\_count may be null. Do not use views in weighted. If likes missing, fall back to reactions \+ comments \+ shares.

### **11.2 Batch (relative velocity)**

After computing raw for every post in **this scan’s combined batch**:

n \= len(batch)  
for each post:  
    lower \= count(other.raw \< this.raw)  
    equal \= count(other.raw \== this.raw)  
    velocity \= 1.0 if n \== 1 else clamp((lower \+ 0.5\*(equal-1)) / (n-1), 0, 1\)

final \= velocity \* niche\_fit \* recency \* platform\_weight

Dedup key (platform, provider\_id) — keep higher final.  

Threshold 0.6 is a **soft preference**, not a view floor. Still surface the best items if all are below 0.6.

### **11.3 Lists written onto ScanBrief (before Gemini)**

* **global:** top 3 by velocity (prefer TT/IG when scores tie)  
* **niche:** top 3–5 with niche\_fit \>= 0.5 (if fewer than 3, take top by final anyway and let Gemini say they are weak)  
* **film\_this candidate:** highest final among TT/IG unless a YT/FB item wins by ≥ 0.15  
* Dedup against post\_ids seen on scans in the last 14 days (skip if already shown, unless it is the only film\_this candidate)

Gemini may **swap** film\_this to another of the top \~15 ids. It may not invent a new post\_id.

————————————————————————————

## **12\. Gemini**

### **12.1 Ladder (best first)**

Skip a rung if the model id is not available on the key.

| Order | Try ids | quality | default daily\_cap |
| :---- | :---- | :---- | :---- |
| 1 | gemini-3.8-flash, gemini-3-flash, gemini-3.5-flash | high | 20 (or 1500 if GEMINI\_LADDER\_JSON says so) |
| 2 | gemini-2.5-flash | medium | 1500 |
| 3 | gemini-2.5-flash-lite, gemini-flash-lite | low | 1000 or 500 |
| 4 | gemini-2.0-flash | low | 1500 |
| 5 | gemini-2.0-flash-lite | low | 1500 |

Caps are **our counters**, Pacific day (America/Los\_Angeles). Official reset: midnight Pacific. We do not scrape AI Studio.

Algorithm for every call:

17. Load usage/geminiDaily/{pacificDate}.  
18. Starting at the first rung with used \< cap and not marked exhausted: POST.  
19. On **429** or our remaining \== 0: mark exhausted, try **next** rung **once**. No retry loop on the same model.  
20. On success: increment used, write usage/events with purpose, model, tokens if present.  
21. If chosen quality is medium/low: attach quality\_warning.  
22. If ladder empty: raise GeminiExhausted with resets\_at. Callers must still return ranked posts / last brief. Never invent prose.

Purposes: expand | synthesize | recap | chat | angles | script.

Never use Gemini Pro.

### **12.2 Synthesis prompt (purpose=\`synthesize\`, one call per paid scan)**

Input: ContextProfile \+ top \~15 scored packets (identity, metrics, caption, scores only — no raw scrape) \+ last 10 playbook bullets.

Output: **JSON only**, no markdown fence. Schema:

{  
  "weekly\_take": "5-8 sentences",  
  "patterns": \["", "", ""\],  
  "ignore": \["", "", ""\],  
  "cards": \[ { "post\_id": "", "why\_it\_works": "2 lines", "format\_guess": "" } \],  
  "film\_this": { "post\_id": "", "why": "" },  
  "playbook\_delta": \["", ""\]  
}

Rules in the system prompt:

* Every post\_id must be in the provided list.  
* Do not invent view counts or claim a platform that is not on the packet.  
* Prefer TT/IG for film\_this unless a secondary platform is clearly stronger.  
* Write for the user’s niche and goal.

Merge cards\[\] onto posts/{id}. Append playbook\_delta as new playbook docs. Persist ScanBrief. Update latest. Recompute usage snapshot \+ alerts. Write-through local cache.

### **12.3 Like → angles → script**

Triggered from Marketing UI or chat tool like\_trend.

23. Set posts/{id}.liked \= true.  
24. Transcript: YouTube → free library. Else if remaining \> 15 and duration ≤ 120s → 1 allowlisted transcript call. Else caption \+ metrics only.  
25. One Gemini call, purpose=angles: 3 original angles that **keep the format** and **change the topic** for this niche. User (or chat) picks one.  
26. One Gemini call, purpose=script: filming\_guide \+ spoken script \+ caption for the chosen angle.  
27. Insert scripts/{uuid} as draft, expires\_at \= now \+ 14d.  
28. Point posts/{id}.script\_id at the draft.

Save: POST save → saved, clear expiry.

### **12.4 Marketing chat**

Grounding: cached ScanBrief \+ optional attached post. Tools (implement as function-calling or a tight JSON action protocol):

* get\_brief  
* get\_post  
* rewrite\_hook  
* captions (exactly 3\)  
* recommend\_tomorrow  
* like\_trend

Refuse to invent metrics. Extra scrape only after the user confirms in-thread. Persist messages under chat/{threadId}/messages.

Overlord must **not** use this chat loop. Overlord calls services.brief.get\_brief() / services.stats.get\_stats() only.

————————————————————————————

## **13\. Usage snapshot and alerts**

After every paid SC call, Gemini call, YouTube unit spend, Like/transcript, or scan: recompute and upsert usage/snapshot.

status ∈ ok | low | exhausted | unknown.

GET /api/stats (or get\_stats()) returns:

{  
  "schema\_version": "1.0",  
  "checked\_at": "ISO-8601",  
  "stale": false,  
  "scrapecreators": {  
    "remaining": 82,  
    "spent\_last\_scan": 3,  
    "spent\_today": 0,  
    "reserve": 15,  
    "usable\_now": 67,  
    "next\_scan\_estimated\_cost": 3,  
    "transcripts\_affordable": true,  
    "status": "ok",  
    "resets": false,  
    "resets\_at": null,  
    "reset\_note": "Credits never expire. When they hit 0 they stay 0 until you buy or claim more."  
  },  
  "gemini": {  
    "tier": "free",  
    "active\_model": "gemini-2.5-flash",  
    "active\_quality": "medium",  
    "quality\_warning": "... or null",  
    "resets": true,  
    "resets\_at": "ISO midnight Pacific as UTC",  
    "resets\_in": "8h 12m",  
    "reset\_note": "All Gemini free-tier RPD counters reset at midnight Pacific Time.",  
    "models": \[  
      { "id": "...", "label": "...", "quality": "high|medium|low", "used\_today": 0, "daily\_cap": 0, "remaining": 0, "status": "ok|exhausted" }  
    \]  
  },  
  "youtube\_data\_api": {  
    "daily\_units\_used": 0,  
    "daily\_quota": 10000,  
    "remaining": 10000,  
    "status": "ok",  
    "resets": true,  
    "resets\_at": "ISO",  
    "reset\_note": "Free quota resets daily at midnight Pacific Time."  
  },  
  "free\_sources": \[  
    { "id": "reddit", "status": "ok", "note": "no credit balance" },  
    { "id": "google\_trends", "status": "ok", "note": "no credit balance" }  
  \],  
  "alerts": \[\]  
}

### **Alert table**

One active alert per (provider, severity). Upsert notifications/{provider}\_{severity}.

**ScrapeCreators (resets=false):**

* remaining ≤ 25 or remaining // 3 ≤ 3 → warning  
* remaining ≤ 15 → warning (reserve; transcripts paused)  
* remaining \< 3 → critical  
* remaining \== 0 → exhausted

**YouTube Data:**

* remaining ≤ 20% of 10000 → warning  
* remaining \== 0 → exhausted; skip free YT pull until resets\_at

**Gemini:**

* active remaining ≤ 20% of that cap → warning (include next model \+ resets\_at)  
* switched off best available high-quality model → warning  
* active quality medium/low → quality warning  
* all rungs exhausted → exhausted; AI pauses; ranked posts still shown

stale=true if snapshot checked\_at is older than 26 hours (daily pull missed).

————————————————————————————

## **14\. Marketing section UI (parent)**

You may ship a minimal HTML fragment or React/Vue components **only if** the parent repo is present. Otherwise export JSON and a short integration note. Do not invent a second product chrome.

Required surfaces:

29. Empty state if no ScanBrief yet.  
30. Board: weekly\_take, Global cards, Niche cards, Film this.  
31. Card actions: Like → pick 1 of 3 angles → show guide/script → Save.  
32. Saved script library (status=saved).  
33. Side chat bound to marketing agent.  
34. Header pills from get\_stats(): SC remaining, each Gemini rung used/remaining, active model, quality warning, resets\_at, YouTube remaining.  
35. Banners from alerts\[\]. Copy must say whether credits **reset** (Gemini/YouTube) or **do not reset** (ScrapeCreators).

Overlord responses about marketing must cite the same scan\_id and must not scrape.

————————————————————————————

## **15\. Dependencies (suggested)**

google-cloud-firestore  
google-auth  
google-generativeai   \# or google-genai — pick one, pin it  
httpx  
pydantic\>=2  
apscheduler  
platformdirs  
pytrends  
youtube-transcript-api  
python-dotenv

No supabase, no anthropic, no Remotion, no Higgsfield SDKs.

————————————————————————————

## **16\. Implementation order**

Build in this sequence. Each step must be independently testable.

36. config.py \+ db/paths.py \+ db/firebase.py (read/write under prefix only; unit-test that a write to users/{uid}/settings is impossible through the client).  
37. packets/schema.py \+ cache/local.py \+ jobs/daily\_pull.py.  
38. context/parse.py with fixtures: Firestore map and a sample context.md.  
39. scrapers/endpoints.py \+ scrapers/client.py with a mock HTTP layer; prove allowlist \+ 48h cache \+ no credit-balance on get\_stats.  
40. Free adapters (YouTube / Reddit / Trends) behind the same normalize interface.  
41. scoring/\* — port relative velocity; golden-test against a fixture copied from AIOS ideas (drop producibility; inject niche\_fit=1, platform\_weight=1 and assert midrank matches).  
42. jobs/scan.py rotation table \+ credit shedding.  
43. agent/gemini.py ladder with a fake transport (429 on rung 1 → rung 2).  
44. Synthesis merge → ScanBrief write → latest → cache write-through.  
45. usage/snapshot.py \+ alerts.py.  
46. Like / script / expire\_drafts.  
47. Chat tools.  
48. Parent route wrappers \+ Marketing UI fragment.  
49. Overlord read helpers (get\_brief, get\_stats) documented in a 20-line OVERLORD.md.

————————————————————————————

## **17\. Acceptance tests (must all pass)**

50. Writing through firebase.py to any path not starting with users/{uid}/marketingRadar raises.  
51. Daily pull is a no-op if cache pulled\_date is today and files exist; deleting one file forces re-fetch.  
52. A paid scan issues ≤ 3 allowlisted ScrapeCreators URLs. A second identical URL inside 48h hits cache (0 credit).  
53. credit-balance is never called from get\_stats().  
54. Relative scoring: three posts with raw 10, 20, 20 produce midrank velocities matching score-ideas.py (\--relative).  
55. final does not include a producibility term.  
56. Call 3 alternates FB vs YT across four successive scan\_index values per the table. Missing FB URLs never call Facebook endpoints.  
57. remaining=15 on scan start → 2 calls (no Call 3). remaining=0 → 0 paid calls.  
58. Gemini 429 on best model → exactly one fallback call; snapshot shows quality warning \+ resets\_at midnight PT.  
59. Synthesis JSON with an unknown post\_id is rejected; ScanBrief is not written.  
60. Like with remaining=15 does not call transcript endpoints.  
61. Draft older than 14 days is deleted; status=saved with old created\_at is kept.  
62. Overlord helper does not import scrapers or gemini.generate.  
63. Package starts on Windows and macOS with the same start\_marketing\_radar() call.

————————————————————————————

## **18\. Failure modes**

| Condition | Behaviour |
| :---- | :---- |
| No context doc | Empty state; do not scrape; alert context\_missing |
| Firestore down, cache warm | Serve cache; stale=true if \>26h |
| Firestore down, cache cold | 503 to UI; overlord says marketing data unavailable |
| ScrapeCreators 402/0 credits | Free APIs \+ recap if prior packet exists; exhausted alert |
| Gemini exhausted | Keep last brief and ranked posts; refuse chat/script/synthesis |
| Malformed Gemini JSON | One repair prompt on a lower rung only; then persist scored cards without weekly\_take and set kind note |
| Transcript fail | Continue Like with caption only |
| Parent userId missing | Refuse to start scheduler |

————————————————————————————

## **19\. What you will not build**

* Standalone Trend Radar app, its own venv-only product, or questionnaire screens  
* New Firebase project, new Supabase project  
* Writes to parent context or any collection outside marketingRadar  
* Video render, publish, Telegram, Claude Code, Remotion, Higgsfield  
* Anthropic / Claude API usage for briefs, chat, or scripts  
* Facebook Graph / Ad Library  
* Comment scrapes, demographics, pagination  
* Dashboard button that enables Gemini paid billing  
* Live credit-balance polling on every page load

————————————————————————————

## **20\. First commit checklist**

* ☐ Namespace client \+ rules comment for the Firebase owner  
* ☐ Context parser \+ daily cache  
* ☐ Allowlisted scraper \+ 48h cache  
* ☐ AIOS relative score \+ niche\_fit \+ platform\_weight  
* ☐ 2-day scan job \+ off-day recap  
* ☐ One synthesis → ScanBrief packet \+ latest  
* ☐ Usage snapshot \+ alerts  
* ☐ Like → draft script → save / 14-day expiry  
* ☐ Marketing chat tools  
* ☐ Overlord read functions  
* ☐ Parent-mounted /api/brief and /api/stats (or documented Firestore reads)

When in doubt: **do not spend a ScrapeCreators credit** and **do not write outside \`marketingRadar/\`**.