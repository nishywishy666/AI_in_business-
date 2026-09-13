"""Credit alerts (spec §13). One active alert per (provider, severity); cleared when numbers recover."""
from __future__ import annotations

import datetime as dt

from ..clock import iso
from ..config import Settings
from ..db import RadarStore
from ..packets import Alert, UsageSnapshot


def compute_alerts(snapshot: UsageSnapshot, settings: Settings, now: dt.datetime) -> list[Alert]:
    alerts: list[Alert] = []
    sc = snapshot.scrapecreators
    cost = max(1, settings.next_scan_estimated_cost)
    if sc.remaining is not None:
        remaining = sc.remaining
        scans_left = remaining // cost
        if remaining == 0:
            alerts.append(_alert("scrapecreators", "exhausted",
                                 "ScrapeCreators credits are at 0. Scans and transcripts are paused. "
                                 "These credits do not reset — buy or claim more to continue."))
        elif remaining < cost:
            alerts.append(_alert("scrapecreators", "critical",
                                 f"Only {remaining} ScrapeCreators credit(s) left — not enough for the next scan "
                                 f"(needs {cost}). Credits do not reset."))
        elif remaining <= settings.reserve:
            alerts.append(_alert("scrapecreators", "warning",
                                 f"ScrapeCreators is inside the {settings.reserve}-credit reserve ({remaining} left). "
                                 "Transcripts are paused so scans can continue. Credits do not reset."))
        elif remaining <= 25 or scans_left <= 3:
            alerts.append(_alert("scrapecreators", "warning",
                                 f"ScrapeCreators credits running low: {remaining} left, about {scans_left} scans. "
                                 "These credits do not reset."))

    yt = snapshot.youtube_data_api
    if yt.remaining == 0:
        alerts.append(_alert("youtube_data_api", "exhausted",
                             "YouTube Data API quota is used up for today. Free YouTube pulls are skipped until "
                             f"{_when(yt.resets_at)} (midnight Pacific). Cached and ScrapeCreators data still work.",
                             resets=True, resets_at=yt.resets_at))
    elif yt.remaining <= 0.2 * yt.daily_quota:
        alerts.append(_alert("youtube_data_api", "warning",
                             f"YouTube Data API quota low: {yt.remaining} of {yt.daily_quota} units left. "
                             f"Resets at {_when(yt.resets_at)} (midnight Pacific).", resets=True, resets_at=yt.resets_at))

    gm = snapshot.gemini
    usable = [m for m in gm.models if m.status == "ok"]
    if gm.models and not usable:
        alerts.append(_alert("gemini", "exhausted",
                             "All Gemini free-tier models are exhausted for today. AI briefs, chat and scripts are "
                             f"paused; ranked posts still show. Resets at {_when(gm.resets_at)} (midnight Pacific).",
                             resets=True, resets_at=gm.resets_at))
    else:
        active = next((m for m in gm.models if m.id == gm.active_model), None)
        warnings: list[str] = []
        if active is not None and active.daily_cap and active.remaining <= 0.2 * active.daily_cap:
            next_model = next((m.label for m in gm.models if m.status == "ok" and m.id != active.id), "none")
            warnings.append(f"{active.label} has {active.remaining} of {active.daily_cap} requests left today; "
                            f"next model: {next_model}.")
        if gm.models and active is not None and active.id != gm.models[0].id:
            warnings.append(f"Switched off {gm.models[0].label} (exhausted) to {active.label}.")
        if warnings:
            alerts.append(_alert("gemini", "warning", " ".join(warnings) + f" Resets at {_when(gm.resets_at)} (midnight Pacific).",
                                 resets=True, resets_at=gm.resets_at))
        if gm.quality_warning:
            alerts.append(_alert("gemini", "info", gm.quality_warning, resets=True, resets_at=gm.resets_at))
    for alert in alerts:
        alert.created_at = now
    return alerts


def sync_notifications(store: RadarStore, alerts: list[Alert], now: dt.datetime) -> None:
    existing = {doc_id: data for doc_id, data in store.list(store.paths.notifications)}
    active_ids = set()
    for alert in alerts:
        active_ids.add(alert.alert_id)
        previous = existing.get(alert.alert_id)
        doc = alert.to_doc()
        if previous and previous.get("created_at"):
            doc["created_at"] = previous["created_at"]
        doc["updated_at"] = iso(now)
        store.set(store.paths.notification(alert.alert_id), doc)
    for doc_id in existing:
        if doc_id not in active_ids:
            store.delete(store.paths.notification(doc_id))


def _alert(provider: str, severity: str, message: str, *, resets: bool = False,
           resets_at: dt.datetime | None = None) -> Alert:
    return Alert(alert_id=f"{provider}_{severity}", provider=provider, severity=severity, message=message,
                 resets=resets, resets_at=resets_at)


def _when(value: dt.datetime | None) -> str:
    return iso(value) if value else "the next reset"
