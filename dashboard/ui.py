"""Serve the Claude Design export untouched, with live data injected at request time (plan 0005).

`support.js` evaluates the page's `<script data-dc-script>` with `new Function(..., src + ";return
Component")`, so `bridge.js` appended inside that tag runs after the class with `Component` in
scope. The only edits to the markup are the `PATCHES` below: literal numbers the mockup hard-coded
in HTML become template bindings the bridge supplies. Every anchor must occur exactly once — a
re-exported design that moves one fails loudly (`tests/test_dashboard_ui.py`), never silently.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .settings import UI_DIR

PAGE = UI_DIR / "Uncle Tony Overlord Dashboard.dc.html"
BRIDGE = Path(__file__).with_name("bridge.js")
SCRIPT_OPEN = '<script type="text/x-dc" data-dc-script>'

# (anchor in the exported template, replacement). Anchors are exact substrings; "\n" is normalised
# to the file's own line ending before matching.
PATCHES: list[tuple[str, str]] = [
    # Overview KPI cards — the four numbers hard-coded in markup
    ('font-weight:500">3 <span style="font-size:14px;color:var(--color-neutral-500);font-family:var(--font-body)">/ 12 covers</span></div>',
     'font-weight:500">{{ kpiBookings }} <span style="font-size:14px;color:var(--color-neutral-500);font-family:var(--font-body)">/ {{ kpiCovers }} covers</span></div>'),
    ('font-weight:500">8</div>\n              <div class="card-meta">{{ impact.calls.compare }}</div>',
     'font-weight:500">{{ kpiCalls }}</div>\n              <div class="card-meta">{{ impact.calls.compare }}</div>'),
    ('font-weight:500">75%</div>\n              <div class="card-meta">6 of 8 calls, no callback requested</div>',
     'font-weight:500">{{ kpiContainment }}</div>\n              <div class="card-meta">{{ kpiContainmentMeta }}</div>'),
    ('font-weight:500">1 <span style="font-size:14px;color:var(--color-neutral-500);font-family:var(--font-body)">/ 13%</span></div>\n              <div class="card-meta">started outside 7:30am–2:30pm Mon–Fri</div>',
     'font-weight:500">{{ kpiOutside }} <span style="font-size:14px;color:var(--color-neutral-500);font-family:var(--font-body)">/ {{ kpiOutsidePct }}</span></div>\n              <div class="card-meta">{{ kpiOutsideMeta }}</div>'),
    # Business context + profile captions
    ("Parsed into 3 columns · last updated just now", "{{ menuMetaLabel }}"),
    ("Menu · 5 items", "{{ menuCountLabel }}"),
    ('<span style="color:var(--color-accent-600)">Demo data</span>', '<span style="color:var(--color-accent-600)">{{ dataSourceLabel }}</span>'),
    ("412 of 1,000 included call minutes used", "{{ usageMinutesLabel }}"),
    ("width:41%;background:var(--color-accent)", "width:{{ usageMinutesPct }}%;background:var(--color-accent)"),
    ('<div style="font-family:var(--font-heading);font-size:18px">Tony Marino</div>', '<div style="font-family:var(--font-heading);font-size:18px">{{ ownerName }}</div>'),
    ('<div style="font-size:12px;color:var(--color-neutral-500)">Owner · Oakleigh VIC</div>', '<div style="font-size:12px;color:var(--color-neutral-500)">{{ ownerSub }}</div>'),
    ('<div style="font-size:11px;color:var(--color-neutral-400);white-space:nowrap;flex:1">Oakleigh, VIC</div>',
     '<div style="font-size:11px;color:var(--color-neutral-400);white-space:nowrap;flex:1">{{ businessLocation }}</div>'),
    # The top-right tag no longer says "Demo data" (plan 0013): it is hidden unless a refresh failed.
    ('<span class="tag tag-outline" style="white-space:nowrap;flex:none">Live prototype</span>',
     '<span class="tag tag-outline" style="{{ headerBadgeStyle }}">{{ headerBadge }}</span>'),
    # Marketing: the scan's own numbers (credits, posts, next scan) belong in one strip above the
    # agent, not strung along the count line — which goes back to saying only how many are shown.
    # "Refresh now" lives in that strip, pushed to its right edge.
    ('<div style="margin-bottom:14px">\n              <span style="font-size:11px;color:var(--color-neutral-500);white-space:nowrap">{{ trendCountLabel }}</span>\n            </div>',
     '<div style="margin-bottom:14px">\n'
     '              <span style="font-size:11px;color:var(--color-neutral-500)">{{ trendCountLabel }}</span>\n'
     '            </div>'),
    ('<div style="display:flex;flex-direction:column;gap:16px">\n'
     '            <div class="card elev-sm mkt-chat" style="display:flex;flex-direction:column">',
     '<div style="display:flex;flex-direction:column;gap:16px">\n'
     '            <div class="card elev-sm dc-mkt-stats" style="padding:12px 16px;gap:8px">\n'
     '              <div style="display:flex;align-items:center;gap:18px;flex-wrap:wrap">\n'
     '                <sc-for list="{{ marketingStats }}" as="ms" hint-placeholder-count="4">\n'
     '                  <div style="{{ ms.style }}">\n'
     '                    <div style="font-size:10px;text-transform:uppercase;letter-spacing:.07em;color:var(--color-neutral-500);white-space:nowrap">{{ ms.label }}</div>\n'
     '                    <div style="font-family:var(--font-heading);font-weight:856;font-size:16px;line-height:1.25;white-space:nowrap">{{ ms.value }}</div>\n'
     '                  </div>\n'
     '                </sc-for>\n'
     '                <button class="btn btn-secondary" style="{{ refreshTrendsStyle }}" onClick="{{ refreshTrends }}" title="{{ refreshTrendsTitle }}">{{ refreshTrendsLabel }}</button>\n'
     '              </div>\n'
     '              <sc-if value="{{ marketingStatsNote }}">\n'
     '                <div style="font-size:11px;color:var(--color-neutral-500);line-height:1.45;border-top:1px solid var(--color-divider);padding-top:8px">{{ marketingStatsNote }}</div>\n'
     '              </sc-if>\n'
     '            </div>\n'
     '            <div class="dc-mkt-row" style="display:flex;gap:14px;align-items:stretch;flex-wrap:wrap">\n'
     '            <div class="card elev-sm mkt-chat" style="display:flex;flex-direction:column;flex:1 1 260px;min-width:0">'),
    # Marketing: a toastie orbiting a ring while the scan loads, in place of an empty column.
    ('<sc-for list="{{ platformGroups }}" as="pg" hint-placeholder-count="3">',
     '<sc-if value="{{ trendsLoading }}">\n              <div class="dc-toastie-loader" style="display:flex;flex-direction:column;align-items:center;gap:14px;padding:52px 0">\n                <div class="dc-toastie-orbit">\n                  <div class="dc-toastie-track"></div>\n                  <div class="dc-toastie-arm"><svg class="dc-toastie" viewBox="0 0 40 40" width="34" height="34" aria-hidden="true"><g class="dc-toastie-steam" fill="none" stroke="var(--color-accent)" stroke-width="1.6" stroke-linecap="round" opacity=".55"><path d="M15 9c-1.6-1.8 1.6-3.2 0-5"/><path d="M22 8.5c-1.6-1.8 1.6-3.2 0-5"/></g><path d="M6.5 26.5 19 13.5l14.5 5.5-12.5 13z" fill="var(--color-accent-300)" stroke="var(--color-accent-700)" stroke-width="1.6" stroke-linejoin="round"/><path d="M19 13.5 33.5 19l-3 3.2L15.8 17z" fill="var(--color-accent-200)" stroke="var(--color-accent-700)" stroke-width="1.4" stroke-linejoin="round"/><path d="M9.5 24.5c2.6 1.4 5 1.1 7.2-.6 2.3 2 4.7 2.2 7.2.5" fill="none" stroke="var(--color-accent-600)" stroke-width="1.7" stroke-linecap="round"/></svg></div>\n                </div>\n                <div style="font-size:12px;color:var(--color-neutral-500)">{{ trendsLoadingLabel }}<span class="dc-dots"></span></div>\n              </div>\n            </sc-if>\n            <sc-for list="{{ platformGroups }}" as="pg" hint-placeholder-count="3">'),
    # My saves / My likes move out from under the chat to a column beside it, centred against
    # its height. The wrapper opened above closes after them.
    ('<div style="display:flex;gap:24px;justify-content:center;padding:16px 0">',
     '<div style="display:flex;flex-direction:column;gap:22px;justify-content:center;padding:6px 2px;flex:none">'),
    ('<div style="font-size:11px;color:var(--color-neutral-500)">{{ likedCount }} liked</div>\n'
     '              </div>\n'
     '            </div>',
     '<div style="font-size:11px;color:var(--color-neutral-500)">{{ likedCount }} liked</div>\n'
     '              </div>\n'
     '            </div>\n'
     '            </div>'),
    # Both chats say what is answering them and what is left: the free model in use, how much of
    # its daily allowance is gone, and the scan credits behind the trend data.
    ('<div class="card-title" style="font-size:15px">Marketing agent</div>',
     '<div class="card-title" style="font-size:15px">Marketing agent</div>\n'
     '              <div class="dc-ai-line" style="{{ aiLineStyle }}" title="{{ aiLineTitle }}"><span style="{{ aiDotStyle }}"></span>{{ aiLine }}</div>'),
    ('<div style="font-size:11px;color:var(--color-neutral-500)">Ask about calls, bookings, trends</div>',
     '<div style="font-size:11px;color:var(--color-neutral-500)">Ask about calls, bookings, trends</div>\n'
     '            <div class="dc-ai-line" style="{{ aiLineStyle }}" title="{{ aiLineTitle }}"><span style="{{ aiDotStyle }}"></span>{{ aiLine }}</div>'),
    # Analytics: Custom opens a calendar. The seg row becomes the popover's anchor; the grid and
    # its selection are built in the bridge, which then asks for period=custom with real dates.
    ('<div style="display:flex;align-items:center;gap:10px">\n            <div class="seg" style="font-size:12px">\n              <sc-for list="{{ periodOptions }}" as="po" hint-placeholder-count="3">\n                <label class="seg-opt" style="white-space:nowrap"><input type="radio" checked="{{ po.active }}" onChange="{{ po.onSelect }}">{{ po.label }}</label>\n              </sc-for>\n            </div>\n          </div>',
     '<div class="dc-period" style="display:flex;align-items:center;gap:10px;position:relative">\n            <div class="seg" style="font-size:12px">\n              <sc-for list="{{ periodOptions }}" as="po" hint-placeholder-count="3">\n                <label class="seg-opt" style="white-space:nowrap"><input type="radio" checked="{{ po.active }}" onChange="{{ po.onSelect }}">{{ po.label }}</label>\n              </sc-for>\n            </div>\n            <sc-if value="{{ calendarOpen }}">\n              <div class="card elev-lg dc-calendar" style="position:absolute;top:40px;right:0;width:290px;z-index:60;padding:12px;gap:10px">\n                <div style="display:flex;align-items:center;justify-content:space-between;gap:8px">\n                  <button class="btn btn-ghost" style="padding:2px 9px;font-size:13px" onClick="{{ calendarPrev }}" title="Previous month">‹</button>\n                  <div style="font-family:var(--font-heading);font-weight:856;font-size:13px">{{ calendarMonth }}</div>\n                  <button class="btn btn-ghost" style="padding:2px 9px;font-size:13px" onClick="{{ calendarNext }}" title="Next month">›</button>\n                </div>\n                <div style="display:grid;grid-template-columns:repeat(7,1fr);gap:2px">\n                  <sc-for list="{{ calendarWeekdays }}" as="wd" hint-placeholder-count="7">\n                    <div style="font-size:10px;text-align:center;color:var(--color-neutral-500);padding:2px 0">{{ wd }}</div>\n                  </sc-for>\n                  <sc-for list="{{ calendarDays }}" as="cd" hint-placeholder-count="35">\n                    <div class="dc-cal-day" style="{{ cd.style }}" onClick="{{ cd.onClick }}">{{ cd.label }}</div>\n                  </sc-for>\n                </div>\n                <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;border-top:1px solid var(--color-divider);padding-top:9px">\n                  <span style="font-size:11px;color:var(--color-neutral-500)">{{ calendarHint }}</span>\n                  <button class="btn btn-primary" style="{{ calendarApplyStyle }}" onClick="{{ calendarApply }}">{{ calendarApplyLabel }}</button>\n                </div>\n              </div>\n            </sc-if>\n          </div>'),
    # Typography: Outfit only. Archivo was the heading face; the bridge's stylesheet moves headings
    # onto Outfit, so stop fetching it. The 100..900 range is the variable axis the weight scale
    # (856 / 577 / 267) needs — a static-weight request would snap to the nearest 100.
    ('<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@100..900&amp;family=Archivo:wdth,wght@75..100,400..900&amp;display=swap" rel="stylesheet">',
     '<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@100..900&amp;display=swap" rel="stylesheet">'),
    # Header search: the export's box was decorative. Bind it and hang a results panel off it —
    # the suggestions are places in the app, and picking one navigates there (see SEARCH_INDEX).
    ('<div style="position:relative"><input class="input" placeholder="Search…" style="width:200px;padding-left:32px">',
     '<div class="dc-search" style="position:relative">'
     '<input class="input" placeholder="{{ searchPlaceholder }}" style="width:200px;padding-left:32px"'
     ' value="{{ searchQuery }}" onInput="{{ setSearchQuery }}" onKeyDown="{{ searchKeyDown }}" onFocus="{{ openSearch }}">'),
    ('stroke-width="1.4" style="position:absolute;left:10px;top:11px"><circle cx="6.8" cy="6.8" r="4.3"></circle><line x1="10" y1="10" x2="13.5" y2="13.5"></line></svg></div>',
     'stroke-width="1.4" style="position:absolute;left:10px;top:11px"><circle cx="6.8" cy="6.8" r="4.3"></circle><line x1="10" y1="10" x2="13.5" y2="13.5"></line></svg>\n'
     '          <sc-if value="{{ searchOpen }}">\n'
     '            <div class="card elev-lg dc-search-panel" style="position:absolute;top:42px;left:0;width:330px;z-index:60;padding:6px;gap:0">\n'
     '              <sc-for list="{{ searchResults }}" as="sr" hint-placeholder-count="4">\n'
     '                <div class="dc-search-row" style="{{ sr.style }}" onClick="{{ sr.onClick }}">\n'
     '                  <div style="font-size:13px">{{ sr.label }}</div>\n'
     '                  <div style="font-size:11px;color:var(--color-neutral-500)">{{ sr.sub }}</div>\n'
     '                </div>\n'
     '              </sc-for>\n'
     '              <sc-if value="{{ searchEmpty }}">\n'
     '                <div style="padding:10px 12px;font-size:12px;color:var(--color-neutral-500)">{{ searchEmptyLabel }}</div>\n'
     '              </sc-if>\n'
     '            </div>\n'
     '          </sc-if>\n'
     '        </div>'),
    # Header bell: a notifications panel built from the records already on the page — waiting
    # callbacks, questions to review, the latest scan, stale data. Each row navigates to its screen.
    ('<button class="btn btn-secondary btn-icon"><svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M8 1.8c-2 0-3.5 1.6-3.5 3.6v2.3c0 .6-.2 1.2-.6 1.7l-.5.7c-.3.4 0 1 .5 1h8.2c.5 0 .8-.6.5-1l-.5-.7c-.4-.5-.6-1.1-.6-1.7V5.4c0-2-1.5-3.6-3.5-3.6z"></path><path d="M6.3 13.2a1.7 1.7 0 0 0 3.4 0"></path></svg></button>',
     '<div class="dc-bell" style="position:relative;flex:none">\n'
     '          <button class="btn btn-secondary btn-icon" onClick="{{ toggleNotifications }}" title="{{ notifTitle }}"><svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M8 1.8c-2 0-3.5 1.6-3.5 3.6v2.3c0 .6-.2 1.2-.6 1.7l-.5.7c-.3.4 0 1 .5 1h8.2c.5 0 .8-.6.5-1l-.5-.7c-.4-.5-.6-1.1-.6-1.7V5.4c0-2-1.5-3.6-3.5-3.6z"></path><path d="M6.3 13.2a1.7 1.7 0 0 0 3.4 0"></path></svg></button>\n'
     '          <span style="{{ notifBadgeStyle }}">{{ notifBadgeCount }}</span>\n'
     '          <sc-if value="{{ notificationsOpen }}">\n'
     '            <div class="card elev-lg dc-notif-panel" style="position:absolute;top:44px;right:0;width:330px;z-index:60;padding:6px;gap:0">\n'
     '              <div style="padding:8px 12px 6px;font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--color-neutral-500)">{{ notifHeading }}</div>\n'
     '              <sc-for list="{{ notifications }}" as="nt" hint-placeholder-count="3">\n'
     '                <div class="dc-search-row" style="{{ nt.style }}" onClick="{{ nt.onClick }}">\n'
     '                  <div style="font-size:13px"><span style="{{ nt.dotStyle }}"></span>{{ nt.title }}</div>\n'
     '                  <div style="font-size:11px;color:var(--color-neutral-500)">{{ nt.sub }}</div>\n'
     '                </div>\n'
     '              </sc-for>\n'
     '              <sc-if value="{{ notifEmpty }}">\n'
     '                <div style="padding:10px 12px;font-size:12px;color:var(--color-neutral-500)">{{ notifEmptyLabel }}</div>\n'
     '              </sc-if>\n'
     '            </div>\n'
     '          </sc-if>\n'
     '        </div>'),
    # Collapsed sidebar: the mascot is the only branding left at 64px wide, so give it room.
    ('<img src="assets/uncle-tony-mascot.svg" style="height:36px;width:auto;flex:none">',
     # align-self is the fix for the stretch: the wrapper is a column flex container, so a child
     # with width:auto gets stretched to its full width while height stays pinned.
     '<img src="assets/uncle-tony-mascot.svg" style="height:52px;width:auto;flex:none;align-self:center">'),
    # Chat bubbles (both agents) gain a mascot and an animated ellipsis, used only while a turn is
    # still in flight — see the thinking states in bridge.js. Per-row bindings, so the bridge decides
    # which rows show them.
    ('<sc-for list="{{ marketingChat }}" as="m" hint-placeholder-count="2">\n'
     '                  <div style="{{ m.bubbleStyle }}">{{ m.text }}</div>\n'
     '                </sc-for>',
     '<sc-for list="{{ marketingChat }}" as="m" hint-placeholder-count="2">\n'
     '                  <div class="dc-chat-row" style="{{ m.rowStyle }}">\n'
     '                    <img class="dc-chat-mascot" src="assets/uncle-tony-mascot.svg" alt="" style="{{ m.mascotStyle }}">\n'
     '                    <div style="{{ m.bubbleStyle }}">{{ m.text }}<span class="dc-dots" style="{{ m.dotsStyle }}"></span></div>\n'
     '                  </div>\n'
     '                </sc-for>'),
    ('<sc-for list="{{ overlordThread }}" as="m" hint-placeholder-count="2">\n'
     '            <div style="{{ m.bubbleStyle }}">{{ m.text }}</div>\n'
     '          </sc-for>',
     '<sc-for list="{{ overlordThread }}" as="m" hint-placeholder-count="2">\n'
     '            <div class="dc-chat-row" style="{{ m.rowStyle }}">\n'
     '              <img class="dc-chat-mascot" src="assets/uncle-tony-mascot.svg" alt="" style="{{ m.mascotStyle }}">\n'
     '              <div style="{{ m.bubbleStyle }}">{{ m.text }}<span class="dc-dots" style="{{ m.dotsStyle }}"></span></div>\n'
     '            </div>\n'
     '          </sc-for>'),
    # Profile: drop the Edit button — there is no edit flow behind it — and the Notifications card,
    # whose toggles persist a preference nothing acts on yet.
    ('<button class="btn btn-secondary">Edit</button>\n', ''),
    ('\n            <div class="card elev-sm">\n'
     '              <div class="card-title">Notifications</div>\n'
     '              <p class="card-body">Choose what Uncle Tony gets pinged about.</p>\n'
     '              <sc-for list="{{ notifRows }}" as="nr" hint-placeholder-count="3">\n'
     '                <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 0;border-bottom:1px solid var(--color-divider)">\n'
     '                  <div style="min-width:0">\n'
     '                    <div style="font-size:13px">{{ nr.label }}</div>\n'
     '                    <div style="font-size:11px;color:var(--color-neutral-500)">{{ nr.sub }}</div>\n'
     '                  </div>\n'
     '                  <div onClick="{{ nr.onToggle }}" style="{{ nr.trackStyle }}"><div style="{{ nr.knobStyle }}"></div></div>\n'
     '                </div>\n'
     '              </sc-for>\n'
     '            </div>\n', '\n'),
    # Business Context is only reachable from Profile, so give it the way back.
    ('<sc-if value="{{ isSetup }}">\n',
     '<sc-if value="{{ isSetup }}">\n'
     '        <div style="margin-bottom:14px">\n'
     '          <button class="btn btn-secondary" onClick="{{ goProfile }}">← Back to profile</button>\n'
     '        </div>\n'),
    # My saves / My likes: the rows were inert. Clicking one opens that trend's script card.
    ('<sc-for list="{{ savedLikedTrends }}" as="sl" hint-placeholder-count="2">\n'
     '                      <div class="card elev-sm">',
     '<sc-for list="{{ savedLikedTrends }}" as="sl" hint-placeholder-count="2">\n'
     '                      <div class="card elev-sm" style="cursor:pointer" onClick="{{ sl.onOpen }}" title="Open this trend">'),
    # Sidebar: the caret next to the business name becomes a profile glyph — it opens a profile menu,
    # so it should read as "you", not as "sort". Same 16-box stroke icons the platform filters use.
    ('<span style="color:var(--color-neutral-400);font-size:10px;flex:none">{{ profileChevron }}</span>',
     '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.4"'
     ' style="color:var(--color-neutral-400);flex:none" aria-label="Profile menu">'
     '<circle cx="8" cy="5.6" r="2.7"/><path d="M2.9 13.7c.6-2.7 2.6-4.1 5.1-4.1s4.5 1.4 5.1 4.1"/></svg>'),
    # Overview: the 7D/30D switch is hard-coded to 7D in the export (readOnly, and 30D has no binding
    # at all). Bind both halves, and make the footer's comparison follow the chosen range.
    ('<label class="seg-opt"><input type="radio" checked="{{ true }}" readOnly="{{ true }}">7D</label>\n'
     '                  <label class="seg-opt"><input type="radio">30D</label>',
     '<label class="seg-opt"><input type="radio" checked="{{ chartRange7 }}" onChange="{{ setChartRange7 }}">7D</label>\n'
     '                  <label class="seg-opt"><input type="radio" checked="{{ chartRange30 }}" onChange="{{ setChartRange30 }}">30D</label>'),
    ('<span style="color:var(--color-accent-400)">↑ {{ chartDeltaPct }}%</span> vs first day in last 7 days</div>',
     '<span style="color:var(--color-accent-400)">{{ chartDeltaArrow }} {{ chartDeltaPct }}%</span> {{ chartDeltaLabel }}</div>'),
    # Overlord: the export gives the panel canned quick questions but no way to type. Same input row
    # the Marketing chat uses, bound to the bridge's /api/overlord/ask call.
    ('        <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px">\n'
     '          <sc-for list="{{ overlordQuickQuestions }}" as="q" hint-placeholder-count="3">\n'
     '            <button class="btn btn-secondary" style="font-size:11px;padding:4px 8px" onClick="{{ q.onClick }}">{{ q.label }}</button>\n'
     '          </sc-for>\n'
     '        </div>',
     '        <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px">\n'
     '          <sc-for list="{{ overlordQuickQuestions }}" as="q" hint-placeholder-count="3">\n'
     '            <button class="btn btn-secondary" style="font-size:11px;padding:4px 8px" onClick="{{ q.onClick }}">{{ q.label }}</button>\n'
     '          </sc-for>\n'
     '        </div>\n'
     '        <div style="display:flex;gap:6px">\n'
     '          <input class="input" placeholder="{{ overlordPlaceholder }}" value="{{ overlordDraft }}" onInput="{{ setOverlordDraft }}" onKeyDown="{{ overlordKeyDown }}">\n'
     '          <button class="btn btn-primary btn-icon" onClick="{{ sendOverlord }}">→</button>\n'
     '        </div>'),
    # Callbacks (plan 0013): every card gets an Open button, and a dialog shows the caller's full
    # number (the card no longer masks it either — present.py) plus the transcript of the call that
    # produced the callback, read from the bootstrap's calls list.
    ('                  <span class="tag {{ cb.statusTagClass }}" style="white-space:nowrap;flex:none">{{ cb.status }}</span>\n'
     '                  <sc-if value="{{ cb.showDoneButton }}"><button class="btn btn-secondary" style="white-space:nowrap;flex:none" onClick="{{ cb.onMarkDone }}">Mark done</button></sc-if>\n',
     '                  <span class="tag {{ cb.statusTagClass }}" style="white-space:nowrap;flex:none">{{ cb.status }}</span>\n'
     '                  <button class="btn btn-primary" style="white-space:nowrap;flex:none;font-size:11px;padding:4px 12px" onClick="{{ cb.onOpen }}" title="Full number and the call transcript">Open</button>\n'
     '                  <sc-if value="{{ cb.showDoneButton }}"><button class="btn btn-secondary" style="white-space:nowrap;flex:none" onClick="{{ cb.onMarkDone }}">Mark done</button></sc-if>\n'),
    ('      </sc-if>\n'
     '\n'
     '      <!-- ============ ANALYTICS (Impact / Voice Ops / Insights) ============ -->',
     '        <sc-if value="{{ callbackModal }}">\n'
     '          <div class="dialog-backdrop" style="backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px)" onClick="{{ closeCallbackModal }}">\n'
     '            <div class="dialog" onClick="{{ stopPropagation }}" style="width:min(700px,100%)">\n'
     '              <div class="dialog-title">Callback · {{ callbackModal.name }}</div>\n'
     '              <div class="dialog-body">\n'
     '                <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">\n'
     '                  <span class="tag tag-neutral">{{ callbackModal.priority }}</span>\n'
     '                  <span class="tag {{ callbackModal.statusTagClass }}">{{ callbackModal.status }}</span>\n'
     '                  <span class="tag tag-outline">{{ callbackModal.reasonLabel }}</span>\n'
     '                </div>\n'
     '                <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:14px">\n'
     '                  <sc-for list="{{ callbackModal.fields }}" as="cf" hint-placeholder-count="4">\n'
     '                    <div style="background:var(--color-surface);border:1px solid var(--color-divider);border-radius:var(--radius-md);padding:9px 11px;min-width:0">\n'
     '                      <div style="font-size:10px;text-transform:uppercase;letter-spacing:.07em;color:var(--color-neutral-500)">{{ cf.label }}</div>\n'
     '                      <div style="font-size:14px;margin-top:2px;overflow-wrap:anywhere">{{ cf.value }}</div>\n'
     '                    </div>\n'
     '                  </sc-for>\n'
     '                </div>\n'
     '                <div style="font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--color-neutral-500);margin-bottom:8px">{{ callbackModal.transcriptHeading }}</div>\n'
     '                <div style="display:flex;flex-direction:column;gap:8px;max-height:44vh;overflow:auto">\n'
     '                  <sc-for list="{{ callbackModal.transcript }}" as="line" hint-placeholder-count="4">\n'
     '                    <div style="display:flex;align-items:flex-start;gap:10px">\n'
     '                      <span style="font-size:11px;color:var(--color-neutral-500);width:34px;flex:none;padding-top:3px">{{ line.ts }}</span>\n'
     '                      <span style="{{ line.speakerStyle }}" class="tag">{{ line.speaker }}</span>\n'
     '                      <span style="font-size:13px;opacity:.9;flex:1">{{ line.text }}</span>\n'
     '                    </div>\n'
     '                  </sc-for>\n'
     '                  <sc-if value="{{ callbackModal.transcriptEmpty }}">\n'
     '                    <div style="font-size:12px;color:var(--color-neutral-500)">{{ callbackModal.transcriptNote }}</div>\n'
     '                  </sc-if>\n'
     '                </div>\n'
     '              </div>\n'
     '              <div class="dialog-actions">\n'
     '                <sc-if value="{{ callbackModal.showDoneButton }}"><button class="btn btn-secondary" onClick="{{ callbackModal.onMarkDone }}">Mark done</button></sc-if>\n'
     '                <button class="btn btn-primary" onClick="{{ closeCallbackModal }}">Close</button>\n'
     '              </div>\n'
     '            </div>\n'
     '          </div>\n'
     '        </sc-if>\n'
     '      </sc-if>\n'
     '\n'
     '      <!-- ============ ANALYTICS (Impact / Voice Ops / Insights) ============ -->'),
    # Marketing cards (plan 0013): the thumbnail box links to the original post (with the packet's
    # thumbnail behind it when there is one) and a ▶ Watch button joins Like / Save.
    ('                      <div style="height:84px;border-radius:var(--radius-sm);background:var(--color-neutral-900);display:flex;align-items:center;justify-content:center;font-size:11px;color:var(--color-neutral-600)">video thumbnail</div>',
     '                      <div class="dc-watch" style="{{ t.thumbStyle }}" onClick="{{ t.onWatch }}" title="{{ t.watchTitle }}"><span style="{{ t.watchLabelStyle }}">{{ t.watchLabel }}</span></div>'),
    ('                        <button class="btn btn-secondary" style="{{ t.saveStyle }}" onClick="{{ t.onSave }}">{{ t.saveGlyph }} Save</button>\n',
     '                        <button class="btn btn-secondary" style="{{ t.saveStyle }}" onClick="{{ t.onSave }}">{{ t.saveGlyph }} Save</button>\n'
     '                        <button class="btn btn-secondary" style="{{ t.watchStyle }}" onClick="{{ t.onWatch }}" title="{{ t.watchTitle }}">▶ Watch</button>\n'),
]


class TemplateDrift(RuntimeError):
    """The exported design no longer contains an anchor exactly once."""


def render_page(template: str | None = None, bridge: str | None = None) -> str:
    html = template if template is not None else PAGE.read_text(encoding="utf-8")
    newline = "\r\n" if "\r\n" in html else "\n"
    for anchor, replacement in PATCHES:
        anchor, replacement = anchor.replace("\n", newline), replacement.replace("\n", newline)
        count = html.count(anchor)
        if count != 1:
            raise TemplateDrift(f"anchor found {count} times (expected 1): {anchor[:80]!r}")
        html = html.replace(anchor, replacement)
    start = html.find(SCRIPT_OPEN)
    if start < 0:
        raise TemplateDrift("data-dc-script block not found")
    end = html.find("</script>", start)
    if end < 0:
        raise TemplateDrift("data-dc-script block is not closed")
    bridge_js = bridge if bridge is not None else BRIDGE.read_text(encoding="utf-8")
    return html[:end] + newline + "// ---- dashboard bridge (dashboard/bridge.js, injected at serve time) ----" + newline + bridge_js + newline + html[end:]


@lru_cache(maxsize=1)
def _rendered(stamp: tuple[int, int]) -> str:
    return render_page()


def cached_page() -> str:
    """Rendered once and held, but keyed on both source files' mtimes: `uvicorn --reload` only
    watches `*.py`, so without this an edit to bridge.js needs a server restart to show up — and
    looks for all the world like the change did not work."""
    return _rendered((PAGE.stat().st_mtime_ns, BRIDGE.stat().st_mtime_ns))


def mount_ui(app: FastAPI, *, cache: bool = True) -> None:
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def page() -> HTMLResponse:
        return HTMLResponse(cached_page() if cache else render_page())

    @app.get("/support.js", include_in_schema=False)
    async def support() -> FileResponse:
        return FileResponse(UI_DIR / "support.js", media_type="application/javascript")

    app.mount("/_ds", StaticFiles(directory=UI_DIR / "_ds"), name="ui-ds")
    app.mount("/assets", StaticFiles(directory=UI_DIR / "assets"), name="ui-assets")
