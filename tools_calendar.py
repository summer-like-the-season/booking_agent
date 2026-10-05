"""Google Calendar access through your personal Gmail account.

The Google Cloud project and OAuth sign-in use the personal account
(BOOKER_EMAIL), because UCSB doesn't allow enabling the Calendar API.

The agent uses this to:
  1. check busy times, so it never books over an existing event
     (on your Gmail calendar, plus your UCSB calendar if you share it; see README)
  2. find the booked event on your Gmail calendar and add guests to it,
     such as your university email
"""
import os
import time
from datetime import datetime, timedelta

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from google_auth import get_credentials

_service = None


def get_service():
    """Return an authorized Calendar API client (sign-in handled in google_auth.py)."""
    global _service
    if _service is None:
        _service = build("calendar", "v3", credentials=get_credentials())
    return _service


def busy_calendar_ids() -> list[str]:
    """Calendars to check for conflicts, from BUSY_CALENDARS in .env (comma-separated)."""
    raw = os.getenv("BUSY_CALENDARS", "primary")
    return [c.strip() for c in raw.split(",") if c.strip()]


def check_busy(start: str, end: str) -> dict:
    """Busy intervals across all BUSY_CALENDARS between two RFC3339 timestamps."""
    ids = busy_calendar_ids()
    body = {"timeMin": start, "timeMax": end, "items": [{"id": c} for c in ids]}
    result = get_service().freebusy().query(body=body).execute()

    busy, problems = [], []
    for cal_id, info in result["calendars"].items():
        if info.get("errors"):
            # e.g. "notFound" when the UCSB calendar isn't shared with this account
            problems.append(f"{cal_id}: {info['errors'][0].get('reason')}")
        busy.extend({**b, "calendar": cal_id} for b in info.get("busy", []))
    busy.sort(key=lambda b: b["start"])
    return {"busy": busy, "calendars_with_errors": problems}


def list_events(start: str, end: str, query: str | None = None) -> list[dict]:
    """Events on the primary (Gmail) calendar between two RFC3339 timestamps."""
    kwargs = dict(
        calendarId="primary",
        timeMin=start,
        timeMax=end,
        singleEvents=True,
        orderBy="startTime",
        maxResults=50,
    )
    if query:
        kwargs["q"] = query
    items = get_service().events().list(**kwargs).execute().get("items", [])
    return [
        {
            "id": e["id"],
            "summary": e.get("summary", "(no title)"),
            "start": e["start"].get("dateTime", e["start"].get("date")),
            "end": e["end"].get("dateTime", e["end"].get("date")),
            "organizer": e.get("organizer", {}).get("email"),
        }
        for e in items
    ]


def _find_event_starting_at(start: str) -> dict | None:
    """The event on the primary calendar that starts at `start` (RFC3339), if any."""
    t = datetime.fromisoformat(start)
    window_start = (t - timedelta(minutes=1)).isoformat()
    window_end = (t + timedelta(minutes=1)).isoformat()
    items = (
        get_service()
        .events()
        .list(calendarId="primary", timeMin=window_start, timeMax=window_end, singleEvents=True)
        .execute()
        .get("items", [])
    )
    for e in items:
        s = e["start"].get("dateTime")
        if s and datetime.fromisoformat(s) == t:
            return e
    return None


def invite_guests(start: str, emails: list[str], wait_seconds: int = 90) -> str:
    """Add guests to the booked event that starts at `start` on the Gmail calendar.

    The booking confirmation can take a little while to reach the calendar,
    so this polls for up to `wait_seconds` before giving up.
    """
    deadline = time.time() + wait_seconds
    event = _find_event_starting_at(start)
    while event is None and time.time() < deadline:
        time.sleep(10)
        event = _find_event_starting_at(start)
    if event is None:
        return (
            f"No event starting at {start} found on the Gmail calendar after {wait_seconds}s. "
            "The booking may not have gone through, or the invitation hasn't arrived yet."
        )

    if event.get("guestsCanInviteOthers", True) is False:
        return (
            f"Found '{event.get('summary')}', but the organizer doesn't allow guests to invite others. "
            "The guests must be added another way (e.g. forward the invitation)."
        )

    attendees = event.get("attendees", [])
    existing = {a.get("email", "").lower() for a in attendees}
    new = [e for e in emails if e.lower() not in existing]
    if not new:
        return f"'{event.get('summary')}' already includes {', '.join(emails)}."

    try:
        get_service().events().patch(
            calendarId="primary",
            eventId=event["id"],
            body={"attendees": attendees + [{"email": e} for e in new]},
            sendUpdates="all",  # emails the invitation to the new guests
        ).execute()
    except HttpError as err:
        return (
            f"Google refused to add guests to '{event.get('summary')}' ({err.status_code}: {err.reason}). "
            "Add the guests by forwarding the invitation instead."
        )
    return f"Invited {', '.join(new)} to '{event.get('summary')}' at {start}."
