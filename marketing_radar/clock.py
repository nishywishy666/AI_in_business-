from __future__ import annotations

import datetime as dt
from typing import Callable
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
UTC = dt.timezone.utc

Clock = Callable[[], dt.datetime]


def utc_now() -> dt.datetime:
    return dt.datetime.now(UTC)


def ensure_aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def iso(value: dt.datetime) -> str:
    return ensure_aware(value).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ensure_aware(parsed)


def pacific_date(now: dt.datetime) -> str:
    return ensure_aware(now).astimezone(PACIFIC).strftime("%Y-%m-%d")


def next_pacific_midnight_utc(now: dt.datetime) -> dt.datetime:
    local = ensure_aware(now).astimezone(PACIFIC)
    next_day = (local + dt.timedelta(days=1)).date()
    midnight = dt.datetime.combine(next_day, dt.time(0, 0), tzinfo=PACIFIC)
    return midnight.astimezone(UTC)


def local_date(now: dt.datetime) -> str:
    return ensure_aware(now).astimezone().strftime("%Y-%m-%d")


def utc_date(now: dt.datetime) -> str:
    return ensure_aware(now).astimezone(UTC).strftime("%Y-%m-%d")


def humanize_delta(delta: dt.timedelta) -> str:
    total_minutes = max(0, int(delta.total_seconds() // 60))
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"
