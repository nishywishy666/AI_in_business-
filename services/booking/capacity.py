"""Service windows, slot ids, alternatives and spoken times (vr_plan.md §8.3, §8.4).

Windows come from config/capacity.yaml (TODO(spec): values not given). A slot document must exist
before a booking can lock it; a missing slot is a booking failure, not an empty room.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from services.common.firestore import BusinessPaths

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_HOURS = ["twelve", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven"]


@dataclass(frozen=True)
class Window:
    days: tuple[str, ...]
    open: dt.time
    close: dt.time
    slot_minutes: int
    seats_total: int

    def applies(self, date: dt.date) -> bool:
        return WEEKDAYS[date.weekday()] in self.days


def parse_windows(raw: list[dict] | None) -> list[Window]:
    windows = []
    for entry in raw or []:
        windows.append(Window(
            days=tuple(str(d).lower()[:3] for d in entry.get("days", [])),
            open=dt.time.fromisoformat(str(entry["open"])), close=dt.time.fromisoformat(str(entry["close"])),
            slot_minutes=int(entry.get("slot_minutes", 30)), seats_total=int(entry["seats_total"]),
        ))
    return windows


def slot_id_for(date: dt.date, time: dt.time) -> str:
    return f"{date.isoformat()}T{time.strftime('%H%M')}"


def slot_times(date: dt.date, windows: list[Window]) -> list[tuple[dt.time, Window]]:
    out: list[tuple[dt.time, Window]] = []
    for window in windows:
        if not window.applies(date):
            continue
        cursor = dt.datetime.combine(date, window.open)
        end = dt.datetime.combine(date, window.close)
        while cursor < end:
            out.append((cursor.time(), window))
            cursor += dt.timedelta(minutes=window.slot_minutes)
    return sorted(out, key=lambda t: t[0])


def in_windows(date: dt.date, time: dt.time, windows: list[Window]) -> bool:
    return any(t == time for t, _ in slot_times(date, windows))


def next_open_day(date: dt.date, windows: list[Window], *, limit_days: int = 14) -> dt.date | None:
    for offset in range(1, limit_days + 1):
        candidate = date + dt.timedelta(days=offset)
        if slot_times(candidate, windows):
            return candidate
    return None


def materialise_slots(windows: list[Window], start: dt.date, days: int, business_id: str) -> dict[str, dict]:
    docs: dict[str, dict] = {}
    for offset in range(days):
        date = start + dt.timedelta(days=offset)
        for time, window in slot_times(date, windows):
            docs[slot_id_for(date, time)] = {"businessId": business_id, "date": date.isoformat(),
                                             "time": time.strftime("%H:%M"), "seatsTotal": window.seats_total,
                                             "seatsBooked": 0}
    return docs


@dataclass(frozen=True)
class Alternative:
    date: dt.date
    time: dt.time

    @property
    def slot_id(self) -> str:
        return slot_id_for(self.date, self.time)


def nearby_options(reader, business_id: str, date: dt.date, time: dt.time, party_size: int,
                   windows: list[Window], *, limit: int = 3) -> list[Alternative]:
    """§8.4: same day ±30 min, then ±60 min, then the next open day at the same time. Only slots
    that genuinely have seats, never outside the windows, at most three."""
    paths = BusinessPaths(business_id)
    base = dt.datetime.combine(date, time)
    candidates: list[Alternative] = []
    for delta in (30, -30, 60, -60):
        moment = base + dt.timedelta(minutes=delta)
        candidates.append(Alternative(moment.date(), moment.time()))
    nxt = next_open_day(date, windows)
    if nxt:
        candidates.append(Alternative(nxt, time))
    out: list[Alternative] = []
    for alt in candidates:
        if alt.date != date and alt.date != nxt:
            continue
        if not in_windows(alt.date, alt.time, windows):
            continue
        doc = reader.get_doc(paths.capacity_slot(alt.slot_id))
        if not doc:
            continue
        if int(doc.get("seatsBooked") or 0) + party_size <= int(doc.get("seatsTotal") or 0):
            if alt not in out:
                out.append(alt)
        if len(out) >= limit:
            break
    return out


# ---- spoken phrasing ---------------------------------------------------------------------------

def speak_time(time: dt.time) -> str:
    hour12 = time.hour % 12
    word = _HOURS[hour12]
    suffix = "in the morning" if time.hour < 12 else ("in the afternoon" if time.hour < 17 else "in the evening")
    if time.minute == 0:
        return f"{word} {suffix}" if hour12 not in (7, 8, 9) or time.hour < 12 else word
    if time.minute == 15:
        return f"quarter past {word}"
    if time.minute == 30:
        return f"half past {word}"
    if time.minute == 45:
        return f"quarter to {_HOURS[(hour12 + 1) % 12]}"
    return f"{word} {time.minute:02d}"


def speak_date(date: dt.date, today: dt.date) -> str:
    if date == today:
        return "today"
    if date == today + dt.timedelta(days=1):
        return "tomorrow"
    day = date.strftime("%A")
    if 0 < (date - today).days < 7:
        return day
    ordinal = _ordinal(date.day)
    return f"{day} the {ordinal} of {date.strftime('%B')}"


def speak_alternatives(alternatives: list[Alternative], today: dt.date, base_date: dt.date) -> str:
    parts = []
    for i, alt in enumerate(alternatives):
        when = speak_time(alt.time)
        if alt.date != base_date:
            when += f" {speak_date(alt.date, today)}"
        elif i == len(alternatives) - 1 or all(a.date == base_date for a in alternatives):
            when += " tonight" if alt.time.hour >= 17 and alt.date == today else ""
        parts.append(when)
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + f", or {parts[-1]}"


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
