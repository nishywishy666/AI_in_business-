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
    ('<span class="tag tag-outline" style="white-space:nowrap;flex:none">Live prototype</span>',
     '<span class="tag tag-outline" style="white-space:nowrap;flex:none">{{ headerBadge }}</span>'),
    # Marketing: a "Refresh now" button beside the trend count, so an empty or still-loading trend
    # list has a manual way out (the bridge otherwise only polls every 60s).
    ('<div style="margin-bottom:14px">\n              <span style="font-size:11px;color:var(--color-neutral-500);white-space:nowrap">{{ trendCountLabel }}</span>\n            </div>',
     '<div style="margin-bottom:14px;display:flex;align-items:center;gap:10px">\n'
     '              <span style="font-size:11px;color:var(--color-neutral-500);white-space:nowrap">{{ trendCountLabel }}</span>\n'
     '              <button class="btn btn-secondary" style="{{ refreshTrendsStyle }}" onClick="{{ refreshTrends }}" title="Re-read the latest trend scan now">{{ refreshTrendsLabel }}</button>\n'
     '            </div>'),
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
def cached_page() -> str:
    return render_page()


def mount_ui(app: FastAPI, *, cache: bool = True) -> None:
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def page() -> HTMLResponse:
        return HTMLResponse(cached_page() if cache else render_page())

    @app.get("/support.js", include_in_schema=False)
    async def support() -> FileResponse:
        return FileResponse(UI_DIR / "support.js", media_type="application/javascript")

    app.mount("/_ds", StaticFiles(directory=UI_DIR / "_ds"), name="ui-ds")
    app.mount("/assets", StaticFiles(directory=UI_DIR / "assets"), name="ui-assets")
