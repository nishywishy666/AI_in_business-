"""Shared dashboard settings (UI/... uploads/metrics.md "Shared settings") from config/dashboard.yaml.

One settings object for every calculation, caption and band. Environment only supplies identity
(BUSINESS_DATASET, MARKETING_USER_ID); every number lives in the YAML so it stays visibly configurable.
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from config import load_yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data" / "business"
UI_DIR = REPO_ROOT / "UI" / "UI mockups project scope"


@dataclass(frozen=True)
class Band:
    good: float
    bad: float


@dataclass
class DashboardSettings:
    currency: str = "AUD"
    business_timezone: str = "Australia/Melbourne"
    opening_hours: dict[str, tuple[dt.time, dt.time]] = field(default_factory=dict)
    ai_cost_per_minute_cents: float | None = None
    manual_minutes_per_call: float = 11.5
    labour_cost_per_hour_cents: int = 4250
    ai_daily_cost_cap_cents: int = 2000
    ai_daily_cost_warn_fraction: float = 0.8
    baseline_per_day: float = 6.0
    stale_after_minutes: int = 60
    failed_statuses: tuple[str, ...] = ("error", "failed", "busy", "no-answer", "canceled")
    callback_age_warn_minutes: int = 120
    included_minutes_per_month: int = 1000
    planned_reasons: tuple[str, ...] = ("large_group", "catering", "complaint", "booking_change")
    forced_reasons: tuple[str, ...] = ("no_data", "allergen_unknown", "unanswered_question", "other")
    bands: dict[str, Any] = field(default_factory=dict)
    business_dataset: str = "uncle_tony"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.business_timezone)

    @property
    def dataset_dir(self) -> Path:
        return DATA_DIR / self.business_dataset

    def band(self, name: str) -> Band:
        raw = self.bands.get(name) or {}
        return Band(float(raw.get("good", 0)), float(raw.get("bad", 0)))

    def band_tolerance(self) -> float:
        return float(self.bands.get("volume_tolerance", 0.2))

    def band_bad_multiple(self) -> float:
        return float(self.bands.get("volume_bad_multiple", 2.0))

    def is_open(self, local: dt.datetime) -> bool:
        window = self.opening_hours.get(local.strftime("%a").lower())
        if window is None:
            return False
        open_at, close_at = window
        return open_at <= local.time() < close_at

    def opening_hours_label(self) -> str:
        """'7:30am–2:30pm Mon–Fri, 8am–3pm Sat' style caption built from the schedule."""
        groups: list[tuple[list[str], tuple[dt.time, dt.time]]] = []
        for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
            window = self.opening_hours.get(day)
            if window is None:
                continue
            if groups and groups[-1][1] == window:
                groups[-1][0].append(day)
            else:
                groups.append(([day], window))
        parts = []
        for days, (open_at, close_at) in groups:
            span = days[0].title() if len(days) == 1 else f"{days[0].title()}–{days[-1].title()}"
            parts.append(f"{clock_label(open_at)}–{clock_label(close_at)} {span}")
        return ", ".join(parts)

    @classmethod
    def from_yaml(cls, raw: dict | None = None, *, env: dict | None = None) -> "DashboardSettings":
        raw = dict(raw if raw is not None else load_yaml("dashboard.yaml") or {})
        env = os.environ if env is None else env
        hours: dict[str, tuple[dt.time, dt.time]] = {}
        for day, spec in (raw.get("opening_hours") or {}).items():
            hours[str(day).lower()[:3]] = (dt.time.fromisoformat(str(spec["open"])), dt.time.fromisoformat(str(spec["close"])))
        classes = raw.get("handoff_classes") or {}
        cost = raw.get("ai_cost_per_minute_cents")
        return cls(
            currency=str(raw.get("currency") or "AUD"),
            business_timezone=str(raw.get("business_timezone") or env.get("TZ_BUSINESS") or "Australia/Melbourne"),
            opening_hours=hours,
            ai_cost_per_minute_cents=float(cost) if cost is not None else None,
            manual_minutes_per_call=float(raw.get("manual_minutes_per_call", 11.5)),
            labour_cost_per_hour_cents=int(raw.get("labour_cost_per_hour_cents", 4250)),
            ai_daily_cost_cap_cents=int(raw.get("ai_daily_cost_cap_cents", 2000)),
            ai_daily_cost_warn_fraction=float(raw.get("ai_daily_cost_warn_fraction", 0.8)),
            baseline_per_day=float(raw.get("baseline_per_day", 6)),
            stale_after_minutes=int(raw.get("stale_after_minutes", 60)),
            failed_statuses=tuple(str(s) for s in (raw.get("failed_statuses") or ["error"])),
            callback_age_warn_minutes=int(raw.get("callback_age_warn_minutes", 120)),
            included_minutes_per_month=int(raw.get("included_minutes_per_month", 1000)),
            planned_reasons=tuple(str(r) for r in (classes.get("planned") or [])),
            forced_reasons=tuple(str(r) for r in (classes.get("forced") or [])),
            bands=dict(raw.get("bands") or {}),
            business_dataset=str(env.get("BUSINESS_DATASET") or "uncle_tony"),
        )


def clock_label(time: dt.time) -> str:
    """8:12am / 2:30pm — no leading zero, lower-case meridiem (matches the mockup)."""
    hour = time.hour % 12 or 12
    suffix = "am" if time.hour < 12 else "pm"
    return f"{hour}:{time.minute:02d}{suffix}" if time.minute else f"{hour}{suffix}"


@lru_cache(maxsize=1)
def get_settings() -> DashboardSettings:
    return DashboardSettings.from_yaml()
