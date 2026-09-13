————————————————————————

name: Budget Trend Radar

overview: "Marketing sub-agent inside the parent dashboard. Reads cached context.md from the shared Firebase once a day, scrapes TikTok+Instagram every 2 days, scores with the AIOS formula, writes one Gemini ScanBrief packet per scan under a reserved Firebase namespace the overlord can also read. All AI is Gemini free tier. No Claude/Anthropic. Not a standalone app."

todos:

  \- id: firebase-namespace

    content: Reserved users/{uid}/marketingRadar/\*\* plus read-only context contract so we do not touch other Firebase areas

    status: pending

  \- id: context-cache

    content: Daily pull of main-dashboard context.md / context doc; parse niche fields; re-cache if missing or older than 24h

    status: pending

  \- id: api-allowlist

    content: ScrapeCreators allowlist client plus free YouTube/Reddit/Trends adapters, 2-day budget, 48h scrape cache

    status: pending

  \- id: normalize-score

    content: Normalize TT/YT/IG/FB (+ Reddit/Trends) and score with AIOS formula (relative velocity x recency x niche\_fit)

    status: pending

  \- id: scan-brief

    content: Every-2-days TT+IG scrape, Call 3 alternating FB/YT Shorts, one Gemini synthesis into a ScanBrief packet

    status: pending

  \- id: offday-recap

    content: Off-day Gemini recap from cache plus free Trends/Reddit (0 ScrapeCreators credits)

    status: pending

  \- id: handoff-packet

    content: Store one ScanBrief packet per scan in Firebase; daily pull then split-cache locally; overlord read-only path

    status: pending

  \- id: usage-stats

    content: usage/snapshot plus credit alerts (low / exhausted / reset time) for Marketing section and overlord

    status: pending

  \- id: like-to-script

    content: Like to Gemini angles/script; saved scripts stay on that user in Firebase; unsaved drafts deleted after 14 days

    status: pending

  \- id: gemini-agent

    content: Marketing-section Gemini chat with tools; overlord can inspect the same cached packet if the user asks

    status: pending

  \- id: dashboard-section

    content: Parent Marketing UI binds latest brief, stats banners, Like/Save, and chat (no standalone Today app)

    status: pending

isProject: false

————————————————————————

# **Budget Trend Radar (marketing sub-agent)**

## **Goal**

A **sub-agent of the larger dashboard**, not a standalone app. The parent already asked the short questionnaire at the start and stored it (most likely context.md / a context doc in the shared Firebase). This agent **reads that context once a day**, caches it, then scrapes **TikTok and Instagram every 2 days**, scores what is working with the algorithm in score-ideas.py (d:\\Editoz Club\\Acceleratoz-aios-feat-portable-install\\tools\\idea-scout\\score-ideas.py), and uses **Gemini free-tier** to write a brief of top trending categories **globally** and **for the user’s niche**: what to film, and optionally a script if they Like a trend.

The **Marketing section** on the dashboard is where the user talks to this agent (board \+ chat \+ Like/Save). The **overlord (main agent)** does not scrape or write briefs. If the user asks the overlord about marketing, it **reads** the same Firebase packet and usage snapshot.

Saved scripts stay in **that user’s Firebase library** under the reserved namespace. Unsaved drafts are deleted after 14 days.

This is **not** a clone of Editoz AIOS. Do not build video generation, Telegram, Claude Code, Remotion, or Higgsfield. No Anthropic key. Auth is the parent’s userId.

flowchart LR  
  MainQ\[Main dashboard questionnaire\] \--\> Ctx\[Firebase context\]  
  Ctx \--\> DailyPull\[Daily pull plus local cache\]  
  DailyPull \--\> Scan\[Scan every 2 days\]  
  Scan \--\> Score\[AIOS score\]  
  Score \--\> Gemini\[Gemini synthesis\]  
  Gemini \--\> Packet\[Firebase ScanBrief packet\]  
  Packet \--\> MktUI\[Marketing dashboard section\]  
  Packet \--\> MktAgent\[Marketing Gemini chat\]  
  Packet \--\> Overlord\[Overlord read-only\]  
  MktUI \--\>|Like or save script| Scripts\[Firebase scripts\]  
  Scan \--\> Stats\[Firebase usage snapshot\]  
  Stats \--\> MktUI  
  Stats \--\> Overlord

## **Who owns what**

* **Main dashboard / overlord:** questionnaire → Firebase context. Can **read** marketingRadar/latest, latest ScanBrief, usage/snapshot, saved scripts. Does **not** scrape, does **not** call Gemini for a new brief, does **not** write this namespace.  
* **Marketing agent:** only writer under users/{userId}/marketingRadar/\*\*. Runs the 2-day scan, scores, Gemini synthesis, Like→script, marketing chat, usage/alerts.  
* **Marketing section UI:** parent-hosted. Binds to latest packet \+ usage snapshot. User chats with the marketing agent here.

## **Context (no questionnaire in this agent)**

Do **not** ask the questionnaire again. Read whatever the main agent stored.

**Daily Firebase pull (once per calendar day):** context \+ latest ScanBrief pointer \+ usage snapshot → local cache. Chat, UI, and overlord answers use that cache. Next day, or if the cache is missing, pull again and re-cache.

**Expected context fields** (parse from context.md or a Firestore context doc; do not invent a second form):

* Niche  
* 3–6 seed keywords / hashtags  
* Platforms they will post on (default assume TikTok \+ Instagram if missing)  
* Format they can produce, or skip if they only want ideas  
* Region / language  
* Goal (followers, leads, authority)  
* Optional Facebook: up to 2 public page URLs and/or 1 group URL  
* Optional: up to 3 competitor handles

If context has **no hashtags**, one optional Gemini expand is allowed. Write the expansion into marketingRadar/contextCache only — **never write back** into the main context doc. If Gemini is down, split keywords locally.

Confirm the exact context path with the main-dashboard owner before first deploy. Default: users/{userId}/context (Firestore) or a Storage object named context.md under that user.

## **Budget rules**

ScrapeCreators: **100 free credits, never expire, 1 credit ≈ 1 request, cached results \= 0**.

* **Scan every 2 days, not daily.** Hard cap **3 live ScrapeCreators calls per scan** (≈ 45 credits / 30 days). Off-days \= 0 credits, recap from cache.  
* Keep a **15-credit reserve** for user-triggered “Like → breakdown/script.” If balance \< 15, skip transcripts and script from caption \+ metrics only.  
* **Never paginate.** Never call comments or audience-demographics. TikTok use\_ai\_as\_fallback on transcripts costs **10 credits** — never enable it.  
* Cache every raw scrape under marketingRadar/scrapeCache. Same URL+params within 48h → do not call again.  
* Refresh ScrapeCreators balance from credits\_remaining on each paid response (free). Do **not** call GET /v1/account/credit-balance on every dashboard refresh — that endpoint may charge 1 credit. Call it only before a scan if there is no cached figure.  
* **Free APIs always run** on scan days (0 ScrapeCreators credits) to thicken the brief.  
* **All AI \= Gemini free tier** (GEMINI\_API\_KEY from Google AI Studio). No ANTHROPIC\_API\_KEY. Claude Pro cannot call this agent.  
* **Model cascade (best first):** see AI model routing (\#ai-model-routing-gemini-only). Track used/remaining per model. On 0 remaining or 429, step down. Medium/low quality raises a **quality warning**. Show **resets\_at** (midnight Pacific).  
* Never use Gemini Pro on the free tier. Never enable paid Gemini billing from this agent.  
* Track every Gemini request in usage/events (model, purpose, tokens if returned). The usage snapshot shows used / remaining / reset per model.

## **Storage decision (packet vs structured)**

**Hybrid. One ScanBrief packet per scan; not one Firebase row for the entire module.**

* A single document for scrapes \+ scripts \+ chat \+ usage will hit the **1 MB Firestore limit**, force huge downloads, and risk overlord/marketing write collisions.  
* Fully normalized SQL-style collections add collision risk in a shared DB and lose the “one packet” handoff other agents want.

**Write:**

1. **One ScanBrief packet per scan** at scans/{scanId} — the document the overlord, Marketing section, and marketing chat treat as “what is happening.” After the daily Firebase pull, Python **splits that packet in local cache** (weekly\_take, global\[\], niche\[\], film\_this, playbook) so chat does not re-read Firebase on every turn.  
2. **Sibling docs only where lifecycle needs queries** (same namespace): scripts (save vs 14-day delete), usage/snapshot \+ events (credit alerts without a live scrape), scrapeCache (48h), contextCache (daily questionnaire snapshot).  
3. **Never put raw scrape JSON inside the ScanBrief.** Raw responses stay in scrapeCache.

If local cache disappears, re-hydrate from latest ScanBrief \+ contextCache, then re-cache.

## **Shared Firebase without colliding**

Use **one reserved prefix**. Only this agent writes under it. Overlord and parent UI may read it. Do **not** create generic top-level names (profiles, scans, notifications) that may already belong to the main app.

users/{userId}/context                 \# main dashboard. READ ONLY  
users/{userId}/marketingRadar/         \# THIS AGENT ONLY  
  contextCache                         \# last daily pull of context \+ parsed fields  
  latest                               \# scanId, generatedAt, nextScanAt  
  scans/{scanId}                       \# one ScanBrief packet  
  posts/{postId}                       \# TrendPacket (Like, chat attach)  
  scripts/{scriptId}                   \# draft | saved; expiresAt on drafts  
  usage/snapshot                       \# same JSON as stats handoff  
  usage/events/{eventId}  
  usage/geminiDaily/{pacificDate}  
  scrapeCache/{requestHash}            \# 48h  
  playbook/{entryId}  
  notifications/{alertId}  
  chat/{threadId}/messages/{id}

Security: marketing credentials write users/{uid}/marketingRadar/\*\* and **read** users/{uid}/context. They cannot write context or any sibling agent path.

## **Handoff format (how data leaves this module)**

Every paid scan writes one versioned **ScanBrief** to scans/{scanId} and updates latest. The Marketing section, marketing chat, and overlord all read that document (via parent-hosted routes or direct Firebase). Same JSON as before; storage is Firebase, not Supabase.

GET /api/brief (latest) and GET /api/brief/{scan\_id} — if the parent dashboard exposes them — return:

{  
  "schema\_version": "1.0",  
  "scan\_id": "2026-09-12",  
  "generated\_at": "2026-09-12T02:00:00Z",  
  "next\_scan\_at": "2026-09-14T02:00:00Z",  
  "profile": { "niche": "...", "platforms": \["tiktok", "instagram"\], "goal": "followers" },  
  "credits": { "remaining": 82, "spent\_this\_scan": 3, "reserve": 15, "source": "scrapecreators" },  
  "usage": { "href": "/api/stats" },  
  "sources\_used": \["tiktok\_trending", "instagram\_hashtag", "youtube\_data\_api", "reddit", "google\_trends"\],  
  "global": \[ { "post\_id": "...", "packet\_ref": "posts/tt\_123" } \],  
  "niche": \[ { "post\_id": "...", "packet\_ref": "posts/ig\_456" } \],  
  "film\_this": { "post\_id": "...", "why": "..." },  
  "playbook\_delta": \["comparison hooks still winning in fitness"\]  
}

Each suggestion is also a **TrendPacket** at posts/{post\_id}:

* Identity: platform, post\_id, url, author, published\_at  
* Metrics: likes, comments, shares, views, duration\_sec  
* Creative: caption, hook, hashtags, sound (if any)  
* Scores: velocity, recency, niche\_fit, final  
* Gemini-filled on scan: why\_it\_works, format\_guess  
* Optional (only after Like): transcript, breakdown, angles (3), chosen\_angle, filming\_guide, script\_id  
* Scripts live under scripts/, not forever on the post unless saved

Why this shape: the Marketing section renders cards from ScanBrief; the overlord can be handed one packet and answer “what should I film”; marketing chat reads the latest brief instead of re-scraping.

## **Dashboard usage stats**

Remaining credits are a first-class stat. Stored at usage/snapshot. It is a **cached usage snapshot**, not a live scrape on every page load. Parent may expose GET /api/stats that returns this document.

{  
  "schema\_version": "1.0",  
  "checked\_at": "2026-09-12T02:01:00Z",  
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
    "quality\_warning": "Using an older Flash model. Generation quality is lower than Gemini 3 Flash.",  
    "resets": true,  
    "resets\_at": "2026-09-13T07:00:00Z",  
    "resets\_in": "8h 12m",  
    "reset\_note": "All Gemini free-tier RPD counters reset at midnight Pacific Time.",  
    "models": \[  
      { "id": "gemini-3-flash", "label": "Gemini 3 Flash", "quality": "high", "used\_today": 20, "daily\_cap": 20, "remaining": 0, "status": "exhausted" },  
      { "id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash", "quality": "medium", "used\_today": 4, "daily\_cap": 1500, "remaining": 1496, "status": "ok" },  
      { "id": "gemini-flash-lite", "label": "Flash-Lite", "quality": "low", "used\_today": 0, "daily\_cap": 1000, "remaining": 1000, "status": "ok" },  
      { "id": "gemini-2.0-flash", "label": "Gemini 2.0 Flash", "quality": "low", "used\_today": 0, "daily\_cap": 1500, "remaining": 1500, "status": "ok" }  
    \]  
  },  
  "youtube\_data\_api": {  
    "daily\_units\_used": 200,  
    "daily\_quota": 10000,  
    "remaining": 9800,  
    "status": "ok",  
    "resets": true,  
    "resets\_at": "2026-09-13T07:00:00Z",  
    "reset\_note": "Free quota resets daily at midnight Pacific Time."  
  },  
  "free\_sources": \[  
    { "id": "reddit", "status": "ok", "note": "no credit balance" },  
    { "id": "google\_trends", "status": "ok", "note": "no credit balance" }  
  \],  
  "alerts": \[\]  
}

status is ok | low | exhausted | unknown. alerts is what the Marketing section and overlord render as banners.

## **Credit alerts**

After every paid ScrapeCreators call, Gemini call, Like/transcript, YouTube request, or scan, recompute alerts and upsert usage/snapshot plus notifications/{alertId}. Do not spam: one active alert per provider+severity. Clear when the number recovers.

**ScrapeCreators (does not reset):**

* remaining ≤ 25 or scans left ≤ 3 → warning: credits running low, \~N scans left, these do not reset  
* remaining ≤ 15 (reserve) → warning: transcripts paused so scans can continue  
* remaining \< next scan cost (3) → critical: not enough for the next scan  
* remaining \= 0 → exhausted: scans and transcripts paused; credits do not reset

**YouTube Data API (resets midnight Pacific):**

* remaining ≤ 20% of daily quota → warning  
* remaining \= 0 → exhausted; skip free YouTube pull until resets\_at; keep cache / ScrapeCreators

**Gemini free tier (per model, resets midnight Pacific):**

* Active model remaining ≤ 20% of cap → warning with next model and reset time  
* Switched off the best model → warning  
* Active quality is medium or low → quality warning  
* Entire ladder empty or 429 → exhausted; AI chat/scripts/briefs pause; ranked posts still show; show exact reset time

How numbers are produced:

* **ScrapeCreators** — credits\_remaining / credits\_charged on the last allowlisted response  
* **Gemini** — increment usage/events on every call (expand | synthesize | recap | chat | script); compare to that model’s daily\_cap; on 429 mark exhausted and step down  
* **YouTube Data API** — units vs 10k/day  
* **Reddit / Trends** — status only

## **AI model routing (Gemini only)**

Numbers stay Python. Models never pick scrape endpoints or invent view counts. One GEMINI\_API\_KEY. Walk a **fixed ladder, best first**, on every request:

4. Count used\_today vs daily\_cap for the current rung (usage/geminiDaily).  
5. If remaining \> 0, call that model.  
6. If remaining \= 0 or Google returns 429, mark the rung exhausted for this Pacific day and try the **next** rung once. No retry loop on the same model.  
7. If chosen rung quality is medium or low, attach quality\_warning.  
8. If the ladder is empty, refuse AI. Ranked scrape results stay. Tell the user the **exact reset time**.

**Ladder (best → older). Skip a row if that model id is not on the key.**

* 1 — gemini-3.8-flash / gemini-3-flash / gemini-3.5-flash — high — default cap 20 (or 1500 if Studio says so)  
* 2 — gemini-2.5-flash — medium — 1500 — “Older Flash. Quality is lower than Gemini 3.”  
* 3 — gemini-2.5-flash-lite / gemini-flash-lite — low — 1000 or 500  
* 4 — gemini-2.0-flash — low — 1500  
* 5 — gemini-2.0-flash-lite — low — 1500

Never use Gemini Pro on the free tier. Caps are env/config (GEMINI\_LADDER\_JSON or per-model \_RPD). Copy numbers from AI Studio Rate Limits (https://aistudio.google.com/rate-limit). Official RPD reset: **midnight Pacific**. We count our calls; we do not scrape AI Studio.

Every AI job uses this ladder: optional context expand, synthesis, recap, chat, angles, script. Store model\_used \+ quality on the row.

## **Which ScrapeCreators APIs we actually need**

Facebook has **no trending-feed endpoint**. FB is page / group / post based. Treat Facebook as a **niche source** (pages or groups named in context), not a global trending source.

**Allowlist — scan (every 2 days, pick 3):**

* Global TikTok — GET /v1/tiktok/get-trending-feed  
* Global YouTube — GET /v1/youtube/shorts/trending  
* Global Instagram — GET /v1/instagram/reels/trending  
* Niche TikTok — GET /v1/tiktok/search/hashtag or .../search/keyword  
* Niche YouTube — GET /v1/youtube/search  
* Niche Instagram — GET /v1/instagram/search/hashtag  
* Niche Facebook — GET /v1/facebook/profile/reels (10 reels / call from a context page URL)  
* Alt Facebook — GET /v1/facebook/group/posts (only if context has a public group URL)

**Allowlist — on Like only (optional, 1 credit, skipped if reserve is low):**

* TikTok — GET /v1/tiktok/video/transcript (never use\_ai\_as\_fallback)  
* Instagram — GET /v2/instagram/media/transcript (video under 2 min)  
* YouTube — prefer free youtube-transcript-api; fallback GET /v1/youtube/video/transcript  
* Facebook — GET /v1/facebook/post/transcript (under 2 min)

**Do not use in v1:** comments, comment replies, audience demographics (26 credits), photos, events, profile search pagination, LinkedIn/X/Pinterest, TikTok live.

## **Free APIs (always on scan days)**

These do not spend ScrapeCreators credits:

* **YouTube Data API v3** (free 10k units/day): niche search \+ video stats  
* **youtube-transcript-api** (free): captions for Liked YouTube items  
* **Reddit** public JSON (/r/{sub}/hot.json): map context niche → 1–2 subreddits  
* **Google Trends** (pytrends): search-demand confirmation for context keywords

Facebook Graph is not useful without app review. Meta Ad Library is a later add-on, not v1.

## **Scan cadence (every 2 days)**

**Priority: TikTok and Instagram on every paid scan.** Facebook and YouTube Shorts share the third credit only. Free YouTube Data API still runs every scan (0 ScrapeCreators credits).

3 ScrapeCreators calls:

* **Call 1 — always TikTok**  
* **Call 2 — always Instagram**  
* **Call 3 — secondary** (Facebook page/group **or** YouTube Shorts trending), **alternating**

Within TT/IG, alternate global vs niche:

* **Day 0:** TikTok trending · Instagram hashtag (niche) · Facebook page reels if a page is in context, else YouTube Shorts trending  
* **Day 2:** TikTok hashtag (niche) · Instagram trending reels · YouTube Shorts trending (or skip if free YT API already covered it — then extra TT/IG keyword)  
* **Day 4:** TikTok keyword (niche) · Instagram hashtag (niche) · Facebook group posts if set, else Facebook page reels, else YT Shorts  
* **Day 6:** TikTok trending · Instagram trending reels · YouTube Shorts trending

Off-days: rescore cache, refresh Google Trends/Reddit \+ free YouTube Data API, **one Gemini recap** (“nothing new spent — here’s what still holds” \+ playbook refresh). No ScrapeCreators.

If credits are in the reserve zone (remaining ≤ 15), drop Call 3 first so TikTok \+ Instagram still run (2 credits). If only 1 credit remains, prefer TikTok.

## **Scoring (free, local — AIOS algorithm)**

Normalize every post to the TrendPacket metric fields. Facebook view\_count can be null — fall back to reactions \+ comments \+ shares.

From score-ideas.py (d:\\Editoz Club\\Acceleratoz-aios-feat-portable-install\\tools\\idea-scout\\score-ideas.py):

* weighted \= likes \+ 3\*comments \+ 5\*shares  
* raw \= weighted / max(0.5, hours\_since\_post)  
* **Use \`--relative\`:** velocity \= percentile of raw within the batch (best in this scrape → \~1.0). View count is not a gate.  
* recency \= max(0.3, 1 \- days/14); unknown date → 0.5  
* niche\_fit from caption/hashtags vs cached context seeds  
* **Do not use** Higgsfield producibility  
* final \= velocity \* niche\_fit \* recency \* platform\_weight  
* Platform weight: TT/IG ×1.0, YouTube/Facebook ×0.85  
* Dedup (source, post\_id) keep higher score. Threshold 0.6 is a soft preference, not a view floor

Lists: **Global** (top 3 by velocity, TT/IG preferred) · **For your niche** (top 3–5, niche\_fit ≥ 0.5) · **Film this** (one pick; prefer TikTok or Instagram unless a Facebook/YouTube item clearly wins). Dedup post\_id seen in the last 14 days.

## **Gemini scan synthesis (one call per paid scan)**

After Python scoring, send cached context \+ top \~15 packets \+ last 10 playbook entries to Gemini. Ask for JSON only:

* weekly\_take — 5–8 sentences on what is working  
* patterns — 3 hook/format/length patterns  
* ignore — 3 things not to copy  
* cards\[\] — post\_id, why\_it\_works (2 lines), format\_guess  
* film\_this — post\_id \+ reason in the user’s niche (prefer TT/IG)  
* playbook\_delta — 2–3 bullets appended to playbook

Write those fields onto the ScanBrief packet and onto posts/{postId}. Marketing section, marketing chat, and overlord read them; they do not re-call Gemini for the same brief.

## **Like → 3 angles \+ filming guide \+ script (Gemini)**

User taps Like on a card in the Marketing section.

9. Mark packet liked: true.  
10. **Breakdown (optional, credit-gated):** YouTube \= free transcript; else 1 ScrapeCreators credit if remaining \> 15 and duration ≤ 120s; else caption \+ metrics only.  
11. **Gemini (cascade), one call:** 3 original angles that keep the **format** and change the topic for this niche; user picks one (or chat picks).  
12. **Gemini:** filming\_guide \+ script (spoken lines \+ caption) for the chosen angle. Insert scripts/{id} as **draft** for that userId, expiresAt \= now \+ 14 days.  
13. **Save vs expire**

* User saves → status=saved, savedAt=now, expiresAt=null. Stays in **that user’s** marketingRadar/scripts forever (until they delete it).  
* User does nothing → stays draft. A daily cleanup deletes drafts where expiresAt \< now. Saved scripts are never auto-deleted.  
* Regenerating creates a new draft; the old draft still expires unless saved.

## **Gemini chat (Marketing section)**

Grounded in the **cached** latest ScanBrief \+ attached post. Same Flash → Lite cascade. Tools:

* get\_brief, get\_post  
* rewrite\_hook, captions (3 options)  
* recommend\_tomorrow  
* like\_trend (runs the Like → angles/script flow)

Refuses to invent metrics. Extra scrape only after explicit confirm. History under marketingRadar/chat/....

If the user asks the **overlord** about marketing, overlord uses get\_brief / usage/snapshot from cache or Firebase. It does not open a second Gemini synthesis.

## **How this lives in the parent workflow**

Python **package** the parent process imports (not its own python app.py server). Parent starts APScheduler so the same process runs the 2-day scan and the daily Firebase pull on Windows and macOS (pathlib only; no hardcoded /Users/... or C:\\).

marketing\_radar/  
  jobs/scan.py                 \# every 2 days  
  jobs/daily\_pull.py           \# Firebase → local cache once a day  
  jobs/expire\_drafts.py        \# delete unsaved scripts after 14 days  
  db/firebase.py               \# namespaced Firestore client  
  cache/local.py               \# split-cache ScanBrief \+ context; re-hydrate if missing  
  scrapers/client.py           \# ScrapeCreators allowlist \+ scrapeCache  
  scrapers/free\_youtube.py  
  scrapers/free\_reddit.py  
  scrapers/free\_trends.py  
  scoring/normalize.py  
  scoring/score.py             \# AIOS formula, relative mode  
  packets/schema.py            \# ScanBrief \+ TrendPacket \+ UsageStats  
  agent/gemini.py              \# cascade \+ synthesize, recap, chat, angles, script

Parent dashboard hosts Marketing UI and optional GET /api/brief \+ GET /api/stats that read Firebase / local cache. Env lives with the parent: FIREBASE\_\*, SCRAPECREATORS\_API\_KEY, GEMINI\_API\_KEY, optional YOUTUBE\_API\_KEY, GEMINI\_LADDER\_JSON.

## **Marketing section UI (parent dashboard)**

14. Open Marketing → load **cached** latest ScanBrief \+ usage snapshot (trigger daily pull if cache stale).  
15. Board from Gemini weekly\_take \+ scored cards with why\_it\_works (global \+ niche \+ film this).  
16. Each card: Like trend → pick 1 of 3 Gemini angles → Script / Guide → **Save script** or leave it (auto-delete in 14 days). Saved library list in this section.  
17. Side chat (marketing agent): rewrite hook, captions, “what should I post tomorrow?”  
18. Header/footer from usage snapshot: ScrapeCreators remaining; each Gemini model used/remaining; active model; quality warning; reset time (midnight PT). YouTube quota left.  
19. Alert banners from alerts\[\].

Overlord, if asked, summarizes the same cached packet and alerts — it does not scrape.

## **What we will not build in v1**

* A standalone Trend Radar app or its own questionnaire  
* Video generation or publishing  
* Telegram, Claude Code, Remotion, Higgsfield  
* Claude / Anthropic  
* Writing into the main context doc or any Firebase path outside marketingRadar/  
* Scraping comments or every competitor every day  
* Facebook “global trending” (the API cannot do it)  
* Calling credit-balance on every dashboard refresh

## **Success criteria**

* Context comes from the main dashboard; this agent never re-asks the questionnaire; Firebase context is pulled at most once a day and cached.  
* Scan every 2 days spends **≤ 3** ScrapeCreators credits plus **one** Gemini synthesis call; Call 3 alternates Facebook page/group and YouTube Shorts.  
* Like produces 3 angles \+ a guide/script via Gemini; transcript only when reserve allows.  
* Saved scripts persist on that user’s marketingRadar/scripts; unsaved drafts are deleted after 14 days.  
* Marketing chat can rewrite hooks/captions and trigger Like without extra scrapes.  
* Marketing section and overlord show the same usage snapshot: Gemini ladder used/remaining, quality warning, reset time, ScrapeCreators remaining without spending extra credits on refresh.  
* User is warned before credits run out, then told when a source is empty and whether it resets.  
* All marketing writes stay under users/{uid}/marketingRadar/\*\*. Overlord can read the latest packet when asked. Other Firebase areas are untouched.  
* Runs as a package inside the parent on Windows and macOS; not a clone of Editoz AIOS.