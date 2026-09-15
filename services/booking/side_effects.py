"""A runtime switch for the two booking side effects that reach the outside world.

Bookings made against a local sink normally use `LogMailer` + `NullCalendar` (vr_plan.md §12.3: a
simulator run must not send real mail or touch the owner's calendar). For a live demo that rule is
exactly backwards — the whole point is to show the email arriving and the event appearing.

This wraps both providers behind a flag that defaults to OFF, so the safe behaviour is unchanged
until someone deliberately turns it on. Only the mailer and the calendar are switched: the booking
row still goes to the local sink, so no real seat is ever consumed and Firestore is never written.
The switch lives here rather than in the websocket handler because `api/voice/ws.py` is forbidden
from knowing the simulator exists (tests/test_sim_isolation.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SideEffectSwitch:
    """Shared mutable flag. `available` is False when credentials for the real providers are absent."""
    enabled: bool = False
    available: bool = False
    reason: str = ""
    sent: list[dict] = field(default_factory=list)  # what the demo actually dispatched, newest last

    def state(self) -> dict:
        return {"enabled": self.enabled and self.available, "available": self.available,
                "reason": self.reason, "sent": self.sent[-10:]}


class SwitchableMailer:
    def __init__(self, off: Any, on: Any, switch: SideEffectSwitch) -> None:
        self.off, self.on, self.switch = off, on, switch

    def send(self, *, to: str, subject: str, body: str) -> None:
        live = self.switch.enabled and self.switch.available
        (self.on if live else self.off).send(to=to, subject=subject, body=body)
        self.switch.sent.append({"kind": "email", "live": live, "to": to, "subject": subject})


class SwitchableCalendar:
    def __init__(self, off: Any, on: Any, switch: SideEffectSwitch) -> None:
        self.off, self.on, self.switch = off, on, switch

    def upsert(self, booking: dict) -> str | None:
        live = self.switch.enabled and self.switch.available
        event_id = (self.on if live else self.off).upsert(booking)
        self.switch.sent.append({"kind": "calendar", "live": live, "eventId": event_id,
                                 "startsAt": booking.get("startsAt")})
        return event_id
