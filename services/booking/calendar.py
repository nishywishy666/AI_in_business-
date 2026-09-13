"""Google Calendar one-way mirror (vr_plan.md V8, §8.3). Firestore is the source of truth.

A Calendar failure must never fail a booking: `calendarSyncedAt` stays null and the retry sweeps
it up later. Event ids derive from the booking's idempotency key so a retry cannot double-book.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Protocol
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)


class CalendarMirror(Protocol):
    def upsert(self, booking: dict) -> str | None: ...


class NullCalendar:
    """Simulator / no-credentials mirror: records intent, mirrors nothing."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def upsert(self, booking: dict) -> str | None:
        self.calls.append(booking)
        return None


class FailingCalendar:
    def upsert(self, booking: dict) -> str | None:
        raise RuntimeError("calendar credentials revoked")


class GoogleCalendarMirror:
    def __init__(self, service_account_info: dict, calendar_id: str, tz: str) -> None:
        self.calendar_id, self.tz = calendar_id, tz
        self._info = service_account_info
        self._service = None

    def _get(self):
        if self._service is None:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            creds = service_account.Credentials.from_service_account_info(
                self._info, scopes=["https://www.googleapis.com/auth/calendar.events"])
            self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def upsert(self, booking: dict) -> str | None:
        event_id = booking["idempotencyKey"]  # hex ⊂ base32hex, valid Calendar id
        start = dt.datetime.fromisoformat(booking["startsAt"])
        if start.tzinfo is None:
            start = start.replace(tzinfo=ZoneInfo(self.tz))
        end = start + dt.timedelta(minutes=int(booking.get("durationMinutes") or 90))
        body = {
            "id": event_id,
            "summary": f"Table for {booking['partySize']} — {booking['name']}",
            "description": f"Phone booking via AI receptionist. Call {booking.get('callId')}. "
                           f"Phone {booking.get('phone') or 'n/a'}. Email {booking.get('email') or 'not captured'}.",
            "start": {"dateTime": start.isoformat(), "timeZone": self.tz},
            "end": {"dateTime": end.isoformat(), "timeZone": self.tz},
        }
        events = self._get().events()
        try:
            events.insert(calendarId=self.calendar_id, body=body).execute()
        except Exception as exc:  # 409 = already mirrored → idempotent update
            if getattr(getattr(exc, "resp", None), "status", None) == 409:
                events.update(calendarId=self.calendar_id, eventId=event_id, body=body).execute()
            else:
                raise
        return event_id


def mirror_after_commit(calendar: CalendarMirror, sink: Any, booking: dict, *, now: dt.datetime) -> bool:
    """Called AFTER the Firestore transaction commits. Never raises."""
    try:
        event_id = calendar.upsert(booking)
    except Exception as exc:
        log.warning("calendar mirror failed; will retry", extra={"call_id": booking.get("callId"), "error": str(exc)})
        return False
    if event_id is not None and hasattr(sink, "write_booking_fields"):
        sink.write_booking_fields(booking["idempotencyKey"], {"calendarEventId": event_id,
                                                             "calendarSyncedAt": now.isoformat()})
    return True


def retry_unsynced(reader: Any, calendar: CalendarMirror, sink: Any, business_id: str, *, now: dt.datetime) -> int:
    """Daily sweep for bookings with calendarSyncedAt == null.
    TODO(spec): the spec says "the daily cron retries it" but does not say where that cron runs
    (Vercel cron → a new route, or the parent's scheduler). Callable from either."""
    from services.common.firestore import BusinessPaths

    synced = 0
    for _, doc in reader.list_docs(BusinessPaths(business_id).bookings, where=[("calendarSyncedAt", "==", None)]):
        if doc.get("status") == "cancelled":
            continue
        if mirror_after_commit(calendar, sink, doc, now=now):
            synced += 1
    return synced
