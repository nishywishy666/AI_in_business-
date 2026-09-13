"""Confirmation + owner emails (vr_plan.md §8.2, §10, Rule 5). emailsSent/{idempotencyKey} is
written with create() semantics through the sink; a duplicate key means "already sent", skip.

TODO(spec): the wording of the confirmation and owner-callback emails is not given; the bodies
below are minimal placeholders for the owner to replace.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Protocol

log = logging.getLogger(__name__)


class Mailer(Protocol):
    def send(self, *, to: str, subject: str, body: str) -> None: ...


class LogMailer:
    """Simulator / tests: records instead of sending."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, *, to: str, subject: str, body: str) -> None:
        self.sent.append({"to": to, "subject": subject, "body": body})


class GmailMailer:
    def __init__(self, user: str, app_password: str) -> None:
        self.user, self.app_password = user, app_password

    def send(self, *, to: str, subject: str, body: str) -> None:
        import smtplib
        from email.message import EmailMessage

        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = self.user, to, subject
        message.set_content(body)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as smtp:
            smtp.login(self.user, self.app_password)
            smtp.send_message(message)


class ResendMailer:
    def __init__(self, api_key: str, sender: str) -> None:
        self.api_key, self.sender = api_key, sender

    def send(self, *, to: str, subject: str, body: str) -> None:
        import httpx

        response = httpx.post("https://api.resend.com/emails", timeout=15,
                              headers={"Authorization": f"Bearer {self.api_key}"},
                              json={"from": self.sender, "to": [to], "subject": subject, "text": body})
        response.raise_for_status()


def send_once(mailer: Mailer, sink: Any, *, key: str, to: str, subject: str, body: str, call_id: str,
              kind: str, now: dt.datetime) -> bool:
    """Idempotent: emailsSent/{key} is created first; if it already exists nothing is sent."""
    record = {"callId": call_id, "to": to, "subject": subject, "kind": kind, "sentAt": now.isoformat()}
    if not sink.write_email_sent(key, record):
        log.info("email already sent; skipping", extra={"call_id": call_id, "key": key})
        return False
    try:
        mailer.send(to=to, subject=subject, body=body)
    except Exception as exc:
        log.error("email send failed", extra={"call_id": call_id, "key": key, "error": str(exc)})
        return False
    return True


def confirmation_email(booking: dict, business_name: str) -> tuple[str, str]:
    subject = f"Your booking at {business_name}"
    body = (f"Hi {booking['name']},\n\nYour table for {booking['partySize']} is booked for "
            f"{booking['spokenWhen']}.\n\nSee you then,\n{business_name}\n")
    return subject, body


def owner_callback_email(callback: dict, business_name: str) -> tuple[str, str]:
    subject = f"[{business_name}] Callback requested: {callback.get('reason')}"
    body = (f"A caller asked for a callback.\n\nName: {callback.get('name') or 'not given'}\n"
            f"Phone: {callback.get('phone') or 'not given'}\nQuestion: {callback.get('question') or '-'}\n"
            f"Reason: {callback.get('reason')}\nCall: {callback.get('callId')}\n")
    return subject, body
