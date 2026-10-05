"""Booking rules for automatic mode, and a log of what has been booked.

In --auto mode nobody approves bookings, so the rules are enforced here in
plain Python before the Book button is pressed. Claude chooses the slot,
but this code decides whether it may be booked.

Rules come from .env:
    AUTO_START_DATE=2026-10-05      first day of the first window
    AUTO_END_DATE=2026-12-11        last day a meeting may be booked
    AUTO_INTERVAL_DAYS=14           window length (one booking per window)
    AUTO_TIME_WINDOW=11:00-13:00    the whole meeting must fit inside this
"""
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

LOG_FILE = "bookings.json"


@dataclass
class Rules:
    start_date: date
    end_date: date
    interval_days: int
    earliest: time
    latest: time
    tz: ZoneInfo

    def window_index(self, d: date) -> int:
        return (d - self.start_date).days // self.interval_days

    def window_bounds(self, index: int) -> tuple[date, date]:
        first = self.start_date + timedelta(days=index * self.interval_days)
        last = min(first + timedelta(days=self.interval_days - 1), self.end_date)
        return first, last

    def all_windows(self) -> list[int]:
        return list(range(self.window_index(self.end_date) + 1))


def _parse_time(s: str) -> time:
    h, m = s.strip().split(":")
    return time(int(h), int(m))


def load_rules() -> Rules:
    missing = [k for k in ("AUTO_START_DATE", "AUTO_END_DATE") if not os.getenv(k)]
    if missing:
        raise RuntimeError(f"Set {', '.join(missing)} in .env to use --auto.")
    earliest, latest = os.getenv("AUTO_TIME_WINDOW", "11:00-13:00").split("-")
    return Rules(
        start_date=date.fromisoformat(os.environ["AUTO_START_DATE"]),
        end_date=date.fromisoformat(os.environ["AUTO_END_DATE"]),
        interval_days=int(os.getenv("AUTO_INTERVAL_DAYS", "14")),
        earliest=_parse_time(earliest),
        latest=_parse_time(latest),
        tz=ZoneInfo(os.getenv("TIMEZONE", "America/Los_Angeles")),
    )


# ---- booking log ----

def load_log() -> list[dict]:
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE) as f:
        return json.load(f)


def record_booking(start: str, end: str, description: str, mode: str):
    log = load_log()
    log.append({
        "start": start,
        "end": end,
        "description": description,
        "mode": mode,
        "booking_url": os.getenv("BOOKING_URL", ""),
        "booked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    })
    with open(LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)


def booked_windows(rules: Rules) -> set[int]:
    """Windows already booked on the CURRENT booking page (BOOKING_URL).

    Bookings made on a different page (e.g. a test calendar) don't count.
    """
    url = os.getenv("BOOKING_URL", "")
    out = set()
    for b in load_log():
        if b.get("booking_url", url) != url:
            continue
        d = datetime.fromisoformat(b["start"]).astimezone(rules.tz).date()
        if rules.start_date <= d <= rules.end_date:
            out.add(rules.window_index(d))
    return out


def open_windows(rules: Rules, today: date) -> list[int]:
    """Windows that still need a booking and haven't fully passed."""
    done = booked_windows(rules)
    return [
        i for i in rules.all_windows()
        if i not in done and rules.window_bounds(i)[1] >= today
    ]


# ---- the gate ----

def check_slot(rules: Rules, start: str, end: str, busy_check) -> str | None:
    """Return None if the slot may be booked, otherwise the reason it may not.

    busy_check(start, end) must return the dict from tools_calendar.check_busy.
    """
    try:
        s = datetime.fromisoformat(start).astimezone(rules.tz)
        e = datetime.fromisoformat(end).astimezone(rules.tz)
    except ValueError:
        return f"Start/end must be RFC3339 timestamps with an offset; got {start!r}, {end!r}."

    if e <= s:
        return "End must be after start."
    if s <= datetime.now(rules.tz):
        return "Slot is in the past."
    if s.date() != e.date():
        return "Slot spans midnight."
    if not (rules.start_date <= s.date() <= rules.end_date):
        return f"Date {s.date()} is outside {rules.start_date} to {rules.end_date}."
    if s.time() < rules.earliest or e.time() > rules.latest:
        return (
            f"{s:%H:%M}-{e:%H:%M} is not inside the allowed time "
            f"{rules.earliest:%H:%M}-{rules.latest:%H:%M}."
        )

    w = rules.window_index(s.date())
    if w in booked_windows(rules):
        first, last = rules.window_bounds(w)
        return f"The window {first} to {last} already has a booking."

    result = busy_check(s.isoformat(), e.isoformat())
    if result["calendars_with_errors"]:
        return (
            "Could not read every calendar (" + "; ".join(result["calendars_with_errors"]) +
            "), so conflicts can't be ruled out. Fix BUSY_CALENDARS in .env."
        )
    if result["busy"]:
        return f"Conflicts with an existing event: {result['busy'][0]}."
    return None
