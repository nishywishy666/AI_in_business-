# Dashboard Metrics: Standalone UI Specification

Use this document as the central specification for building an AI receptionist and booking dashboard in any UI builder, design tool or application framework. It is self-contained: no repository, local file, existing application, database vendor or voice provider is required. Map your data to the logical entities below.

## Builder instructions

Build three tabs: **Impact**, **Voice Ops** and **Insights**. Implement P0 first. Keep metric IDs stable across calculations, cards and tooltips. Use one shared settings object and one calculation definition per metric. This document defines intended behaviour, not the implementation status of any existing system.

| Priority | Meaning |
| --- | --- |
| P0 | Required initial release; main dashboard scope |
| P1 | Optional extension after P0 is functional and connected |
| P2 | Later release; keep outside the initial UI |
| Cut | Deliberately excluded from the dashboard |

| Tab | Audience | Type | Tile budget | Initial layout |
| --- | --- | --- | --- | --- |
| Impact | Business owners and decision-makers | Executive | 3–5 core; 7 hard ceiling | 7 tiles + 4 controls/captions |
| Voice Ops | Operators and technical teams | Operational | 7–9 | 8 tiles; handoff is one combined tile |
| Insights | Business owners and service teams | Action queue | 3–5 | 3 tiles + dashboard-wide freshness banner |

Keep executive and operational views separate. A tile must support a decision; formula inputs belong in captions or an expandable derivation. On narrow screens, preserve the stated order and stack content without hiding primary values or actions.

## Platform-neutral data contract

Names below are logical field names, not required storage paths. An adapter may translate any API, database, spreadsheet or uploaded dataset into these entities. Use stable IDs to avoid duplicate counting.

| Entity | Required fields / semantics |
| --- | --- |
| `calls` | `id`, `startedAt`, `durationSecs`, `outcomes`, `status`, `taskResult` (success/failure/unknown), `coherenceResult` (success/failure/unknown), `knowledgeGaps` (question and askedAt); optional `measuredCost`, currency and provider credits |
| `turns` | `id`, `callId`, timestamp, `llmTtfbMs`; optional caller-finish time, agent-audio-start time and `replyGapMs` |
| `bookings` | `id`, `callId`, `createdAt`, `partySize`, `confirmationEmail` status; confirmation timing if arrival-time compliance is displayed |
| `callbacks` | `id`, `callId`, `createdAt`, `status` (including open), `reason` |
| `reviews` | Question/group ID, status (pending/approved/dismissed), reviewedAt and approved answer where applicable |
| Refresh metadata | `asOf`: timestamp of the last successful data refresh; retain per-source timestamps if feeds refresh separately |
| Settings | Currency, business time zone, opening hours, cost assumptions, daily cap, baseline, failure states, handoff classes, stale limit and metric bands |

Data readiness is determined by each integration: **Connected** means required data is available; **Needs capture** means fields are missing; **Estimated** means an assumption is used; **Unavailable** means the calculation cannot currently run. Never claim a metric is built or connected merely because its card exists. Label sample datasets **Demo data** visibly.

## Shared settings

| Setting | Initial value / requirement |
| --- | --- |
| `currency` | AUD example default; use one consistent currency per calculation |
| `business_timezone` | Required; use for dates, day boundaries, opening hours and the heatmap |
| `opening_hours` | Required weekly schedule, including overnight hours and business exceptions where applicable |
| `ai_cost_per_minute_cents` | Required for estimated AI cost; no prescribed value |
| `manual_minutes_per_call` | 11.5 minutes; editable, unvalidated assumption |
| `labour_cost_per_hour_cents` | 4,250 cents in AUD; editable, unvalidated assumption. Reconfigure if currency changes |
| `ai_daily_cost_cap` | AUD 20.00 example default; warning fraction 0.80; reconfigure in another currency |
| `baseline_per_day` | Required positive business-specific call baseline |
| `stale_after_minutes` | 60 minutes |
| `failed_statuses` | Explicit set of normalized call failure states |
| `credit_price` | Optional currency per provider credit; required only for credit-based measured cost |
| `avg_spend_per_cover_cents` | Optional scenario-only input; AUD 5,000 example, never measured revenue |
| Missing metric bands | Configure 1.13, 2.12, 2.13 where no comparison is used, and callback age for 3.4 |

The listed bands are initial dashboard rules, not universal industry guarantees. Keep assumed thresholds visibly identified and configurable. What-if adjustments are temporary scenario state with a reset-to-defaults action; they do not alter measured records.

### Shared definitions and units

| Term / symbol | Definition | Unit |
| --- | --- | --- |
| Call | One voice session | calls |
| Contained call | A Call that produced no Callback Request | calls |
| Knowledge Gap | A question the Receptionist could not answer from the business data | gap/question |
| Covers | Sum of party sizes across Bookings | people/covers |
| `N` | Calls handled in the selected period; “÷ calls” always uses this count | calls |
| `N_c` | Contained calls in that period | calls |
| `M` | Connected minutes in the period: `Σ durationSecs ÷ 60` | minutes |
| `r_ai` | AI cost per connected minute | cents per minute |
| `m_s` | Staff minutes one call would take | minutes per call |
| `w` | Staff cost per hour | cents per hour |
| `S`, `F` | Calls graded “success” and “failure”, respectively | calls |
| `k` | Desired percentile, e.g. 50 or 90 | percentile number |
| `n` | Agent turns with a latency sample in the period | turns |
| `d` | Days in the selected period | days |
| `B` | Baseline calls per day | calls per day |

Rate formulas below return fractions; multiply by 100 to display percentages. Monetary formulas using cents return cents; divide by 100 to display the corresponding currency unit. Apply the calculation and empty-state rules in this specification. Formulas for excluded items remain undefined unless explicitly given.

## 1. Impact

**Purpose:** show in five seconds whether the Receptionist pays for itself.

**Hero:** Net saving (1.7). Keep 1.1 and 1.2 directly beneath it as proof.

**Display order:** hero, with connected minutes as caption and derivation strip beneath → volume → business won → containment → cost control → what-if sliders beside the assumption controls.

### P0: shipping tiles (7)

| ID | Metric / question answered | Formula | Data source | Target / band |
| --- | --- | --- | --- | --- |
| 1.7 | **Net saving (hero)** — Does it pay for itself? | `labour cost avoided − total AI cost` | Derived from 1.4–1.6 and configured AI rate | Above 0 means it pays; “estimate” badge on the hero |
| 1.1 | **AI cost per handled enquiry** — What does one AI-handled call cost? | `(M × r_ai) ÷ N` | `durationSecs`; config `ai_cost_per_minute_cents` | “estimate” badge |
| 1.2 | **Staff cost per enquiry** — What would the same call cost if staff answered? | `(m_s ÷ 60) × w` | Config `manual_minutes_per_call`, `labour_cost_per_hour_cents` | AUD 42.50/h and 11.5 min/call are ASSUMPTIONS; estimate presentation |
| 1.3 | **Calls handled (today / this week)** — How busy is the line? | Count of Calls in the period (`N`) | `calls` | Add “vs previous period” arrow; currently a threshold gap |
| 1.8 | **Containment rate** — What share of calls needed no person? | `N_c ÷ N` | `outcomes` | Good ≥70%; Bad <50%; middle band 50% to <70% |
| 1.12 | **Bookings and covers booked** — What business did it bring in? | `count(Bookings)`; `Σ party size` | `bookings` | Add “vs previous period” arrow |
| 1.14 | **Calls answered outside opening hours** — How many calls would have been missed? | `calls starting outside opening_hours ÷ N` | `startedAt`; settings `opening_hours` and `business_timezone` | Show the share and underlying count; no numeric band defined |

Display 1.14 as a percentage with its underlying call count in a caption.

### P0: controls and captions, not tiles

| ID | Element | Formula / behaviour | Placement and rationale |
| --- | --- | --- | --- |
| 1.16 | **What-if sliders** | Recompute the hero from scenario inputs; defaults come from shared settings | Same visual block as hero. A user can change AUD 42.50 and see the result recompute. It is a control. |
| 1.4 | **Connected minutes** | `M = Σ durationSecs ÷ 60` | Caption under hero. Input to 1.1 and 1.7; no independent action. |
| 1.5 | **Staff hours avoided** | `(N_c × m_s) ÷ 60` | Expandable “how this is calculated” strip beneath 1.7; intermediate step. |
| 1.6 | **Labour cost avoided** | `Staff hours avoided × w` | Same derivation strip; intermediate step. |

### Impact derivation and assumptions

```text
Connected minutes       = M = Σ durationSecs ÷ 60
Total AI cost           = M × r_ai
AI cost per enquiry     = (M × r_ai) ÷ N
Staff cost per enquiry  = (m_s ÷ 60) × w
Staff hours avoided     = (N_c × m_s) ÷ 60
Labour cost avoided     = Staff hours avoided × w
Net saving              = (Staff hours avoided × w) − (M × r_ai)
```

Only **contained calls** save staff time: a Callback Request still needs a person. Subtract the AI cost of **all calls**, including escalated calls. Do not use all Calls in place of `N_c` in staff hours avoided.

The hero rests on two assumptions with no evidence behind them: **11.5 minutes per call** and **AUD 42.50 per hour** (`w = 4,250` cents/hour). Keep the “estimate” badge on 1.7 itself, not just 1.1 and 1.2, and keep sliders in the same visual block. The AI rate must be supplied in settings; no default is prescribed.

### P1: build once every P0 is real

| ID | Metric | Formula | Required data | Target / slot |
| --- | --- | --- | --- | --- |
| 1.13 | **Call-to-booking conversion** | `calls with outcome Booked ÷ N` | `calls.outcomes` | Needs a config band before shipping; beside 1.12 |
| 1.11 | **AI spend vs daily cap** | `today's AI cost ÷ daily cap` | `calls.measuredCost` + setting `ai_daily_cost_cap` in the dashboard currency; requires measured cost and a configured cap | Cap AUD 20.00; warn at 80% (AUD 16.00); cost-control row |

1.13 counts Calls with outcome `Booked`, not the number of Booking records. 1.11 supplies a visible cost-governance ceiling. Measured cost requires the cost capture and credit-price work described under 2.18; use `credit_price` only when converting provider credits to money.

### P2: deferred from Impact

| ID | Metric | Formula / reason deferred |
| --- | --- | --- |
| 1.10 | **Monthly running bill** | `Σ AI cost over the calendar month`. Meaningful only after 1.11's measured cost lands; requires sufficient calendar-month data; label an incomplete month as month to date. The original per-call half is Cut (1.10a). |
| 1.9 | **All-in cost per connected minute** | `measured AI cost ÷ connected minutes`. Moved to Voice Ops as **2.18 (P1 there)**; an engineering benchmark, not an owner decision. This is a cross-reference, not an additional tile. |

### Cut from Impact

| ID | Metric | Formula / reason |
| --- | --- | --- |
| 1.15 | **Revenue from AI bookings** | `covers × assumed spend per cover`. Example scenario assumption: AUD 200 per table of four (equivalent to AUD 50 per cover). Presenting this as measured revenue is too attackable. Keep `avg_spend_per_cover_cents` only as a **scenario input inside the what-if panel**, not an outcome tile. |
| 1.10a | **Cost per call** | Duplicates 1.1's `total AI cost ÷ calls`; cut half of original 1.10. |

## 2. Voice Ops

**Purpose:** show technical reliability across Business, Quality and Operations, with a separate handoff tile.

**Hero:** none. Every tile uses **Good / Watch / Needs attention**, with an icon and words, never colour alone. **Bad** in the formulas maps to **Needs attention** in the UI. Values between Good and Bad bounds are Watch.

**Display order:** Business → Quality → Operations → Handoffs to a person.

### P0: shipping tiles (8)

| ID | Tier | Metric / question | Formula | Data source | Bands |
| --- | --- | --- | --- | --- | --- |
| 2.1 | Business | **Task success** — Did callers get what they rang for? | `S ÷ (S + F)`; omit “unknown” | `calls.taskResult` | Good ≥85%; Watch 70% to <85%; Bad <70% |
| 2.2 | Business | **Containment** — Did the call avoid needing a person? | `N_c ÷ N`, as 1.8 | `outcomes` | Good ≥70%; Watch 50% to <70%; Bad <50% |
| 2.3 | Quality | **Latency p50 / p90 (LLM time to first byte)** — How fast does the model start replying? | Nearest-rank percentile over agent turns | `turns.llmTtfbMs` | p50: Good <1.5 s, Watch 1.5–3 s, Bad >3 s. p90: Good <3 s, Watch 3–5 s, Bad >5 s |
| 2.6 | Quality | **Conversation coherence** — Does the conversation make sense end to end? | `success ÷ (success + failure)` for `conversation_coherent` | `calls.coherenceResult` | Good ≥90%; Watch 75% to <90%; Bad <75% |
| 2.7 | Ops | **Error rate** — How many calls broke? | `calls with status in failed_statuses ÷ N` | `calls.status` | Good <3%; Watch 3–10%; Bad >10% |
| 2.8 | Ops | **Call volume** — Is traffic normal or spiking? | `(N ÷ d) ÷ B` | `calls`; config `baseline_per_day` | Good within baseline ±20%; Bad >2× baseline; Watch otherwise |
| 2.9 | Ops | **Fallback rate** — How often did it hit a question it could not answer? | `calls with ≥1 Knowledge Gap ÷ N` | `calls.knowledgeGaps` | Good <10%; Watch 10–20%; Bad >20%; **Bad value is an ASSUMPTION** |
| 2.10 + 2.11 | Handoffs | **Planned vs forced handoff (one two-part tile)** — Are escalations by design, or is the agent giving up? | `Callback Requests per reason class ÷ N` | `callbacks.reason` | Planned healthy 30–40%; Forced Good <10%, Watch 10–20%, Bad >20%; **Bad value is an ASSUMPTION** |

2.10 and 2.11 must never be separate tiles. High planned handoff means policy is working; high forced handoff signals the agent breaking mid-flow. This specification does not define full Watch/Bad bands for the planned rate outside its healthy 30–40% range.

2.2 intentionally duplicates 1.8. Use the identical label, definition and band on both tabs; use “Containment rate” for both.

### Voice Ops formulas and classification

```text
Task success = S ÷ (S + F)

Latency p_k = value at rank ceil((k ÷ 100) × n)
              in the ascending sorted list of turn latencies

Volume multiple = (N ÷ d) ÷ B
Good  if abs(multiple − 1) ≤ 0.20
Bad   if multiple > 2
Watch otherwise

Fallback rate = count(Calls with at least one Knowledge Gap) ÷ N
Planned handoff rate = count(Callback Requests with planned reasons) ÷ N
Forced handoff rate  = count(Callback Requests with forced reasons) ÷ N
```

`ceil` rounds up to the next whole number; percentile ranks are one-based. p50 is the typical turn; p90 describes how slow the worst tenth of turns get. Use agent-turn samples, not per-call averages. “Unknown” task-success calls are neither `S` nor `F`. Coherence uses the same success/failure denominator for its named criterion. Map provider failure states into the configured `failed_statuses` set.

Use the following configurable handoff reason classes:

| Class | Meaning | Reasons |
| --- | --- | --- |
| Planned | Policy says a person handles it | `large_party`, `booking_change`, `catering`, `complaint` |
| Forced | The Receptionist could not cope | `unanswered_question`, `other` |
| Unclassified | Any other reason; never silently lose a new code | All other reason codes |

The handoff numerators count **Callback Requests**, whereas containment counts **Calls with no Callback Request**. Do not silently replace one with the other.

### P1: build once every P0 is real

| ID | Tier | Metric | Formula | Required data | Target / context |
| --- | --- | --- | --- | --- | --- |
| 2.4 | Quality | **End-to-end reply latency p50 / p95** | For each turn, `agent audio start − caller finish`; take p50/p95 of reply gaps | `turns.replyGapMs`; capture and persist caller-finish and agent-audio-start timing | First-audio target ≤1.5 s. Configure separate reply-gap bands; this target does not define p50/p95 bands |
| 2.12 | Ops | **Abandoned rate** | `calls with outcome Abandoned ÷ N` | `calls.outcomes` | Needs a band before shipping |
| 2.13 | Ops | **Average call duration** | `Σ durationSecs ÷ N` (seconds/call) | `calls.durationSecs` | Needs a band or trend arrow |
| 2.14 | Ops | **Confirmation email delivery** | `Bookings with email Sent ÷ Bookings` | `bookings.confirmationEmail` | Target 100%, arriving in <30 s. The rate alone does not establish arrival time |
| 2.18 | Ops | **All-in cost per connected minute (moved from 1.9)** | `measured AI cost ÷ M`; measured cost from `provider credits × credit_price` | `calls.measuredCost` and currency; alternatively provider credits plus `credit_price`. Estimated until measured cost is connected | No universal cost band; configure a business-specific threshold or comparison |

2.4 measures caller-perceived reply latency; 2.3 measures one pipeline stage. Persist turn-level reply gaps and use the nearest-rank definition for p50/p95. Annotate 2.3: **“model time to first byte, not caller-perceived wait”**.

2.12 earns P1 because callers hanging up mid-booking represents lost revenue as well as quality loss. If all five Voice Ops P1 items land, move **2.13 and 2.18 into a collapsed “more detail” row**, rather than the main grid.

### Cut from Voice Ops

| ID | Metric | Definition / exclusion reason |
| --- | --- | --- |
| 2.5 | **Latency by stage (STT / LLM / tool / TTS)** | No aggregate formula defined. Keep stage diagnostics in a dedicated troubleshooting view. |
| 2.15 | **Tool failure rate by tool** | Exact denominator/aggregation not defined. Requires per-tool invocation and failure capture; revisit when a tool breakdown supports a decision. |
| 2.16 | **Scripted menu Q&A accuracy** | Example: 10/10 on 10 fixed questions. Keep this in an evaluation report; it is not an ongoing live metric. |
| 2.17 | **Email capture attempts per Booking** | Aggregation not specified. UX diagnostic with no owner action; ask in the retro. |
| — | **Per-agent leaderboards, WER, sentiment trends, A/B branch comparison, cohort retention** | Outside the initial dashboard scope. Introduce only with a defined user decision and measurement contract. |

## 3. Insights

**Purpose:** turn what callers asked into actions the owner can approve.

**Hero:** unanswered questions, most asked first.

**Display order:** unanswered questions (left, larger) → who is still waiting → why calls went to a person.

Every insight shows its **source, timestamp and evidence**. Stale data must look visibly different from live data. Anything leading to action needs an **Approve / Dismiss** control.

### P0: shipping tiles (3)

| ID | Metric / question | Formula / display | Data source | Interaction / requirement |
| --- | --- | --- | --- | --- |
| 3.1 | **Questions it couldn't answer (hero)** — What business data is missing? | Group questions ignoring case, spacing and punctuation; evidence is distinct Calls that asked; show source call IDs and time last asked | `calls.knowledgeGaps` | Approve (add an answer) / Dismiss on each; unreviewed first |
| 3.4 | **Open callbacks and oldest wait** — Who is still waiting for a call back? | `count(callbacks with status open)`; `now − oldest open createdAt` | `callbacks` | Needs a configured age threshold; requires callback status and creation time |
| 3.2 | **Why calls went to a person** — What keeps needing the owner? | Count Callback Requests per reason, tagged planned / forced; retain Unclassified reasons | `callbacks.reason` | Bars carry visible counts |

For each normalized question `q`:

```text
Evidence(q) = count(distinct call IDs asking q)
Last asked(q) = latest asking time for q
Open callbacks = count(Callback Requests whose status is open)
Oldest wait = now − min(createdAt among open Callback Requests)
```

Sort unreviewed questions first, then by evidence count descending, then last-asked time descending. Normalize case, repeated whitespace and punctuation consistently, while preserving original wording for display. When there are no open callbacks, show count 0 and oldest wait as “None waiting”.

3.4 takes precedence over 3.2 because a real customer is waiting for a call; it is the most operationally urgent dashboard item.

### P0: chrome, not a tile

| ID | Element | Formula | Data / rule |
| --- | --- | --- | --- |
| 3.3 | **Data freshness** — Can I trust what I am seeing? | Warn when `now − asOf > stale_after_minutes` | `asOf` + config `stale_after_minutes` = **60 minutes**; global dashboard-wide banner, excluded from tile budget |

### P1 / P2

| ID | Priority | Metric | Formula / display | Data source / condition |
| --- | --- | --- | --- | --- |
| 3.6 | P1 | **Busiest call times (weekday × hour heatmap)** | Count Calls grouped by weekday and hour, in the **business time zone** | `calls.startedAt`. Supports a concrete rostering decision; no numeric band specified |
| 3.5 | P2 | **Review backlog (pending / approved / dismissed)** | Count reviews grouped by status | Requires persisted `reviews`; governance detail; no band defined |

### Cut from Insights

| ID | Metric | Reason |
| --- | --- | --- |
| 3.7 | **Top topics callers ask about** | Requires captured topic labels; overlaps 3.1 and 3.2. Topic grouping/aggregation formula not specified. |

## Required configuration before release

Six surviving rows have neither a band nor a comparison. Supply the following or drop them to caption text. Confirm the required fields are connected, then configure the presentation and thresholds.

| ID | Priority | Fix |
| --- | --- | --- |
| 1.3 | P0 | “vs previous period” arrow |
| 1.12 | P0 | “vs previous period” arrow |
| 1.13 | P1 | Band in shared dashboard settings |
| 2.12 | P1 | Config band |
| 2.13 | P1 | Config band or trend arrow |
| 3.4 | P0 | Age threshold; **warn above 2 h open is an example, not an approved threshold** |

Use the comparison rules below. Missing bands and example limits must not be presented as validated operational thresholds.


## Calculation, filtering and UI states

These platform-neutral rules complete the implementation contract:

- Use a selected interval `[start, end)` in the business time zone. Count calls by `startedAt`, bookings by `createdAt`, and turn samples by their timestamps. Classify containment for selected Calls using linked Callback Requests. For handoff rates, count requests linked to those same selected Calls. Display callback reason counts with an explicit period label.
- Open callbacks and oldest wait are a **current backlog**, regardless of the historical date filter. Label them “Currently open”. The daily cap is “Today”; the monthly bill is the calendar month or explicitly “Month to date”.
- Compare volume and bookings with an immediately preceding interval of equal elapsed length. Show absolute change; show percentage change as `(current − previous) ÷ previous × 100` only when previous is positive. If both are zero, show “No change”; if previous is zero and current is positive, show “No prior baseline”. For rates, show percentage-point change when comparing rates.
- For volume multiple, `d` is elapsed interval duration in days (`elapsed seconds ÷ 86,400`), and `B` must use the same duration convention. Require `d > 0` and `B > 0`.
- Zero denominator, no latency samples, missing required inputs or unknown cost produce **No data**, not zero or a Good badge. Genuine zero counts display 0. Exclude absent latency samples and show sample size. Do not silently treat missing durations or party sizes as zero; mark affected aggregates incomplete.
- Aggregate first, then round for display. Use currency codes, consistent precision, explicit latency units and percentages. Keep full precision for band comparisons. Do not combine currencies without a documented conversion rate and timestamp.
- Estimated AI cost is `M × r_ai`. Measured AI cost is the sum of normalized monetary costs, or provider credits multiplied by a configured credit price. Label the method; never mix the two silently. The initial Impact derivation uses estimated AI cost.
- On each tile show label, value, period, applicable band/comparison and data state. A tooltip or detail panel exposes formula, units, input counts, settings and freshness. Use text plus icons, not colour alone.
- Support loading, empty, partial, stale and error states. Keep stale values visible with their timestamp and warning. Refresh failures must not overwrite values with zeros or advance `asOf`.
- For question approval, collect or confirm the answer, persist the review and reflect the saved result. Dismiss persists dismissal. Show pending and failed-save states; do not imply a saved change when no writable integration exists.

## Implementation sequence and acceptance checklist

1. Map and validate the data contract; connect settings and date filters.
2. Implement shared calculations, data states and the **18 P0 tiles**, plus the Impact controls/captions and freshness banner.
3. Connect the question review actions and current callback backlog. Resolve required bands/comparisons before release.
4. Add P1 metrics only after P0 works. With all P1 items enabled, there are 9 Impact, 13 Voice Ops and 4 Insights tiles. Keep Voice Ops duration (2.13) and cost per minute (2.18) in collapsed “more detail”.
5. Keep P2 and Cut items out of the initial grid. ID 1.9 is an alias/cross-reference to 2.18, not an extra tile. IDs 2.10 and 2.11 share one tile.

Verify that containment matches across tabs; unknown grades do not affect task-success denominators; staff time avoided uses contained Calls only; AI cost includes escalated Calls; percentile calculations use turn samples; unknown handoff reasons remain visible; estimate labels stay attached to affected values; and scenario changes recompute the hero without changing source records.

## Additional excluded metrics

| Metric(s) | Exclusion / future prerequisite |
| --- | --- |
| Upsell attach rate, upsell revenue, average ticket size | Outside the receptionist/booking scope; require ordering and transaction data |
| Router confidence; answer source (cache hit / database hit / not found) | Architecture-specific diagnostics; require an actual router/cache and recorded provenance |
| Confirmation email open rate | Requires email-open tracking; distinct from confirmation delivery |
| Language mix | Requires reliable language capture |

No calculation contract is prescribed for these excluded items. Add one only when extending the scope with a clear user decision and suitable data.
