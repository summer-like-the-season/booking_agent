"""Notification emails, sent from your Gmail through the Gmail API."""
import base64
import os
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build

from google_auth import get_credentials

_service = None


def _gmail():
    global _service
    if _service is None:
        _service = build("gmail", "v1", credentials=get_credentials(interactive=False))
    return _service


def _send(subject: str, body: str) -> str:
    to = os.getenv("NOTIFY_EMAIL", "").strip()
    if not to:
        return "No NOTIFY_EMAIL set; no email sent."
    msg = EmailMessage()
    msg["To"] = to
    msg["From"] = os.getenv("BOOKER_EMAIL", "me")
    msg["Subject"] = subject
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    _gmail().users().messages().send(userId="me", body={"raw": raw}).execute()
    return f"Email sent to {to}."


def _now() -> str:
    tz = os.getenv("TIMEZONE", "America/Los_Angeles")
    return f"{datetime.now(ZoneInfo(tz)):%a %b %d, %Y %I:%M %p} ({tz})"


def notify_booked(slot_description: str, mode: str = "manual") -> str:
    """Email NOTIFY_EMAIL that a slot was just booked."""
    return _send(
        f"Booked: {slot_description}",
        f"Your booking agent just booked a meeting ({mode} run).\n\n"
        f"Slot:       {slot_description}\n"
        f"Booked as:  {os.getenv('BOOKER_EMAIL')}\n"
        f"Booked at:  {_now()}\n"
        f"Booking page: {os.getenv('BOOKING_URL')}\n\n"
        f"A calendar invitation for {os.getenv('INVITE_EMAILS') or 'your guests'} "
        f"should follow separately if the guest step succeeded.\n",
    )


def notify_error(error_text: str) -> str:
    """Email NOTIFY_EMAIL that an automatic run failed."""
    return _send(
        "Booking agent: automatic run failed",
        f"The scheduled booking run at {_now()} stopped with an error:\n\n"
        f"{error_text}\n\n"
        "Nothing was booked in this run unless an earlier email said so.\n"
        "Full details are in logs/auto.log in the project folder.\n",
    )
