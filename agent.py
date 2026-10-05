"""A booking agent: Claude + tools + a loop.

Usage:
    python agent.py --dry-run "Book a slot every 2 weeks, any weekday, between 11am and 1pm, from Oct 5 for 8 weeks"
    python agent.py "...same request..."        # real run; asks y/N before each booking
    python agent.py --auto --dry-run            # test the automatic rules from .env without booking
    python agent.py --auto                      # unattended: books slots that pass the rules (used by the scheduler)
"""
import argparse
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic
from dotenv import load_dotenv

load_dotenv()  # reads .env before anything else uses os.getenv

import booking_rules as rules_mod
import tools_calendar as cal
import tools_notify
from google_auth import get_credentials
from tools_booking import BookingBrowser

MODEL = "claude-sonnet-5-5"
MAX_STEPS = 80  # hard stop so a confused agent can't loop forever

# ---------------------------------------------------------------------------
# 1. Tool definitions: what Claude sees. Name + description + JSON schema.
#    The descriptions matter a lot -- they are how the model knows when to use each tool.
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "name": "check_my_busy_times",
        "description": "Busy intervals across the user's calendars (Gmail, plus UCSB if shared). Use before choosing a slot so you never book over an existing event. If calendars_with_errors is non-empty, mention it in your final summary.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "RFC3339 with offset, e.g. 2026-10-05T00:00:00-07:00"},
                "end": {"type": "string", "description": "RFC3339 with offset"},
            },
            "required": ["start", "end"],
        },
    },
    {
        "name": "list_my_events",
        "description": "List events on the user's own calendar in a time range, optionally filtered by text. Use to verify a booking appeared.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "RFC3339 with offset"},
                "end": {"type": "string", "description": "RFC3339 with offset"},
                "query": {"type": "string", "description": "Optional text to match in event titles/descriptions"},
            },
            "required": ["start", "end"],
        },
    },
    {
        "name": "open_booking_page",
        "description": "Open the booking page and return an accessibility snapshot (text outline of headings, buttons, fields).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "snapshot_page",
        "description": "Return the current accessibility snapshot of the booking page.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "click",
        "description": "Click an element on the booking page by its accessible role and name as shown in the snapshot (e.g. a time-slot button, a 'Next week' button). Returns the new snapshot. Cannot press the final Book button.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Accessible name, copied from the snapshot"},
                "role": {"type": "string", "enum": ["button", "link", "tab", "option", "radio", "checkbox"], "default": "button"},
                "exact": {"type": "boolean", "description": "Require exact name match", "default": False},
                "nth": {"type": "integer", "description": "0-based index if several elements share the name"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "fill_field",
        "description": "Type a value into a form field on the booking page, found by its label.",
        "input_schema": {
            "type": "object",
            "properties": {
                "label": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["label", "value"],
        },
    },
    {
        "name": "confirm_booking",
        "description": "The ONLY way to press the final booking button. In normal runs the user approves in the terminal; in automatic runs the booking rules are checked in code and the booking is refused if the slot breaks them. Call after the slot is selected and the form is filled.",
        "input_schema": {
            "type": "object",
            "properties": {
                "slot_description": {"type": "string", "description": "e.g. 'Tue Oct 7, 11:30am-12:00pm'"},
                "start": {"type": "string", "description": "Slot start, RFC3339 with offset, e.g. 2026-10-07T11:30:00-07:00"},
                "end": {"type": "string", "description": "Slot end, RFC3339 with offset"},
                "button_name": {"type": "string", "description": "Accessible name of the final button", "default": "Book"},
            },
            "required": ["slot_description", "start", "end"],
        },
    },
    {
        "name": "invite_guests_to_booking",
        "description": "After a booking is confirmed, add the invite emails as guests on the booked event in the user's Gmail calendar. Waits up to ~90s for the event to appear.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "Start of the booked slot, RFC3339 with offset, e.g. 2026-10-07T11:30:00-07:00"},
            },
            "required": ["start"],
        },
    },
]


def invite_emails() -> list[str]:
    return [e.strip() for e in os.getenv("INVITE_EMAILS", "").split(",") if e.strip()]


# ---------------------------------------------------------------------------
# 2. Tool dispatch: what actually runs when Claude asks for a tool.
# ---------------------------------------------------------------------------
def run_tool(name: str, args: dict, browser: BookingBrowser, rules: rules_mod.Rules | None) -> str:
    if name == "check_my_busy_times":
        return json.dumps(cal.check_busy(args["start"], args["end"]))
    if name == "list_my_events":
        return json.dumps(cal.list_events(args["start"], args["end"], args.get("query")))
    if name == "open_booking_page":
        return browser.open_page()
    if name == "snapshot_page":
        return browser.snapshot()
    if name == "click":
        return browser.click(args["name"], args.get("role", "button"), args.get("exact", False), args.get("nth"))
    if name == "fill_field":
        return browser.fill(args["label"], args["value"])
    if name == "confirm_booking":
        # Automatic mode: the rules are checked here in code, not left to Claude.
        if rules is not None:
            reason = rules_mod.check_slot(rules, args["start"], args["end"], cal.check_busy)
            if reason:
                print(f"  [rules] refused {args['slot_description']}: {reason}")
                return (
                    f"Refused by the booking rules: {reason} Nothing was booked. "
                    "Close the dialog, then look for another qualifying slot or move on."
                )
            print(f"  [rules] {args['slot_description']} passes all rules.")

        booked_before = len(browser.bookings)
        output = browser.confirm_booking(args["slot_description"], args.get("button_name", "Book"))
        if len(browser.bookings) > booked_before:
            mode = "automatic" if browser.auto else "manual"
            rules_mod.record_booking(args["start"], args["end"], args["slot_description"], mode)
            # Notify in code, not via a tool, so an email goes out for every real booking
            # whether or not Claude remembers to ask for one.
            try:
                status = tools_notify.notify_booked(args["slot_description"], mode)
            except Exception as e:
                status = f"Notification email failed ({type(e).__name__}: {e})."
            print(f"  [notify] {status}")
            output += f"\n\n{status}"
        return output
    if name == "invite_guests_to_booking":
        emails = invite_emails()
        if not emails:
            return "INVITE_EMAILS is empty in .env; nobody to invite."
        if browser.dry_run:
            return f"DRY RUN: would invite {', '.join(emails)} to the event starting {args['start']}."
        return cal.invite_guests(args["start"], emails)
    raise ValueError(f"Unknown tool {name}")


def auto_request(rules: rules_mod.Rules, windows: list[int]) -> str:
    """The plain-English request for an automatic run, built from the .env rules."""
    lines = []
    for i in windows:
        first, last = rules.window_bounds(i)
        lines.append(f"- {first:%a %b %d} to {last:%a %b %d}")
    return (
        f"Book one meeting in each of these date windows. The whole meeting must fall between "
        f"{rules.earliest:%I:%M %p} and {rules.latest:%I:%M %p} on any day, and must not clash "
        f"with my calendar:\n" + "\n".join(lines) + "\n\n"
        "This is an unattended scheduled run: nobody is watching. If anything is unclear "
        "(the page looks wrong, a form asks for something unexpected), book nothing and explain why."
    )


def system_prompt() -> str:
    tz = os.getenv("TIMEZONE", "America/Los_Angeles")
    today = datetime.now(ZoneInfo(tz))
    return f"""You are a scheduling agent acting for {os.getenv('BOOKER_FIRST_NAME')} {os.getenv('BOOKER_LAST_NAME')} <{os.getenv('BOOKER_EMAIL')}>.
Today is {today:%A, %B %d, %Y}. All times are in {tz} (current UTC offset {today:%z}).

Your job: book slots on a Google Calendar booking page according to the user's request.

How to work:
- Open the booking page and read snapshots to find open slots. The page usually shows one week at a time; use its navigation buttons to move between weeks.
- Split the requested date range into the requested intervals (e.g. consecutive 2-week windows from the start date). Book at most ONE slot per window unless told otherwise.
- A slot qualifies only if it starts inside the requested time-of-day range AND does not overlap anything from check_my_busy_times.
- Prefer the earliest qualifying slot in each window unless the user says otherwise.
- To book: click the slot, fill required form fields with the booker's details above, then call confirm_booking with the slot's exact start and end. Never try to press the final button any other way.
- If confirm_booking is refused by the booking rules, accept that: close the dialog and look for another slot in the same window, or move on.
- Guests to include: {', '.join(invite_emails()) or '(none)'}. If the booking form has a field for guests or additional attendees, enter them there. Otherwise, after each confirmed booking, call invite_guests_to_booking with the slot's start time.
- If a required field asks for information you don't have, stop and report it rather than guessing.
- If a window has no qualifying slot, note it and move on.
- Finish with a short summary: each window, what was booked, whether the guests were invited, and whether the notification email went out (or why nothing was booked)."""


# ---------------------------------------------------------------------------
# 3. The agent loop.
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("request", nargs="?", help="What to book, in plain English (not used with --auto)")
    parser.add_argument("--dry-run", action="store_true", help="Go through everything except the final Book click")
    parser.add_argument("--auto", action="store_true",
                        help="Unattended run: book without asking, but only slots that pass the rules in .env")
    opts = parser.parse_args()

    rules = None
    if opts.auto:
        print(f"\n=== Automatic run {datetime.now():%Y-%m-%d %H:%M} ===")
        rules = rules_mod.load_rules()
        windows = rules_mod.open_windows(rules, datetime.now(rules.tz).date())
        if not windows:
            # Every window is booked or past: stop before spending anything on the API.
            print("Nothing left to book: every window is booked or has passed.")
            return
        request = auto_request(rules, windows)
    else:
        request = opts.request or input("What should I book? ")

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment

    # Do the Google sign-in first, before the booking browser opens, so the
    # sign-in tab (in your normal browser) isn't hidden behind a waiting window.
    # Scheduled runs can't click through a sign-in page, so they fail with a clear error instead.
    print("Checking Google sign-in (a browser tab opens if you need to approve)...")
    # A person at a terminal can always sign in; scheduled runs (launchd, GitHub) have no terminal.
    get_credentials(interactive=sys.stdin.isatty())
    print("Google sign-in OK. Opening the booking page...")

    browser = BookingBrowser(
        os.environ["BOOKING_URL"],
        dry_run=opts.dry_run,
        auto=opts.auto,
        headless=os.getenv("HEADLESS", "false").lower() == "true",
    )
    messages = [{"role": "user", "content": request}]

    try:
        for _ in range(MAX_STEPS):
            response = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=system_prompt(),
                tools=TOOLS,
                messages=messages,
            )
            # Keep Claude's turn (text + tool requests) in the history.
            messages.append({"role": "assistant", "content": response.content})

            for block in response.content:
                if block.type == "text" and block.text.strip():
                    print(f"\nClaude: {block.text}")

            if response.stop_reason != "tool_use":
                break  # Claude is done (or hit max_tokens)

            # Run every tool Claude asked for and send the results back.
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                print(f"  -> {block.name}({json.dumps(block.input)})")
                try:
                    output, is_error = run_tool(block.name, block.input, browser, rules), False
                except Exception as e:
                    output, is_error = f"{type(e).__name__}: {e}", True
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                    "is_error": is_error,
                })
            messages.append({"role": "user", "content": results})
        else:
            print(f"\nStopped after {MAX_STEPS} steps.")
    finally:
        browser.close()
        if browser.bookings:
            print("\nBooked:", *browser.bookings, sep="\n  - ")


if __name__ == "__main__":
    import sys
    import traceback

    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as exc:
        traceback.print_exc()
        # In scheduled runs nobody sees the terminal, so email the error.
        if "--auto" in sys.argv and "--dry-run" not in sys.argv:
            try:
                print(tools_notify.notify_error(f"{type(exc).__name__}: {exc}"))
            except Exception as mail_exc:
                print(f"Could not email the error either: {mail_exc}")
        sys.exit(1)
