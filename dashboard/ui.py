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
