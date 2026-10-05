"""Watch the booking page for newly released slots; run the agent only when something changes.

Google doesn't notify outsiders when someone adds availability, so this checks the
page on a schedule instead. The check uses no AI and costs nothing; the Claude agent
(agent.py --auto) runs only when slots appear that weren't there last time.

Usage:
    python watch.py --show       # print the slots the watcher can see; changes nothing
    python watch.py --dry-run    # full check; if there are new slots, run the agent as a dry run
    python watch.py              # full check; if there are new slots, run the agent for real
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

import booking_rules as rules_mod
from tools_booking import BookingBrowser

STATE_FILE = "watch_state.json"
MONTHS_TO_SCAN = int(os.getenv("WATCH_MONTHS", "3"))

DAY_NAME = re.compile(
    r"^\s*\d{1,2},\s*(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b", re.I
)
TIME = re.compile(r"\b\d{1,2}(?::\d{2})?\s?[ap]\.?m\.?(?=\W|$)", re.I)
MONTH_YEAR = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b"
)


def _label(button) -> str:
    return (button.get_attribute("aria-label") or button.inner_text() or "").strip()


def _times_on_page(page) -> set[str]:
    snap = page.locator("body").aria_snapshot()
    return {re.sub(r"\s+", "", m.group(0)).lower() for m in TIME.finditer(snap)}


def scan(browser: BookingBrowser, verbose: bool = False) -> set[str]:
    """Return the set of 'Month YYYY | day | time' entries visible over the next few months."""
    page = browser.page
    browser.open_page()
    found = set()

    for m in range(MONTHS_TO_SCAN):
        page.wait_for_timeout(800)
        header = MONTH_YEAR.search(page.locator("body").aria_snapshot())
        month = header.group(0) if header else f"month+{m}"

        days = page.get_by_role("button", name=DAY_NAME)
        enabled = [i for i in range(days.count()) if days.nth(i).is_enabled()]
        if verbose:
            print(f"{month}: {len(enabled)} clickable day(s)")

        for i in enabled:
            day = days.nth(i)
            label = _label(day)
            try:
                day.click()
                page.wait_for_timeout(700)
            except Exception:
                continue
            times = _times_on_page(page)
            for t in times:
                found.add(f"{month} | {label} | {t}")
            if verbose and times:
                print(f"  {label}: {', '.join(sorted(times))}")

        if m < MONTHS_TO_SCAN - 1:
            nxt = page.get_by_role("button", name=re.compile(r"next month", re.I))
            if nxt.count() == 0:
                break
            nxt.first.click()

    return found


def load_state() -> set[str]:
    """Slots seen at the last check. Starts fresh if BOOKING_URL has changed since then."""
    if not os.path.exists(STATE_FILE):
        return set()
    with open(STATE_FILE) as f:
        state = json.load(f)
    if state.get("booking_url") != os.getenv("BOOKING_URL"):
        return set()
    return set(state.get("slots", []))


def save_state(slots: set[str]):
    with open(STATE_FILE, "w") as f:
        json.dump({"checked_at": datetime.now().isoformat(timespec="seconds"),
                   "booking_url": os.getenv("BOOKING_URL"),
                   "slots": sorted(slots)}, f, indent=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--show", action="store_true", help="Just print what the watcher sees")
    parser.add_argument("--dry-run", action="store_true", help="Run the agent as a dry run if slots change")
    opts = parser.parse_args()

    stamp = f"[watch {datetime.now():%Y-%m-%d %H:%M}]"

    if not opts.show:
        rules = rules_mod.load_rules()
        if not rules_mod.open_windows(rules, datetime.now(rules.tz).date()):
            print(f"{stamp} Every window is booked or past. Nothing to watch.")
            return

    browser = BookingBrowser(
        os.environ["BOOKING_URL"],
        dry_run=True,
        headless=os.getenv("HEADLESS", "false").lower() == "true",
    )
    try:
        current = scan(browser, verbose=opts.show)
    finally:
        browser.close()  # must close before agent.py opens the same browser profile

    if opts.show:
        print(f"\nTotal: {len(current)} slot entr{'y' if len(current) == 1 else 'ies'} visible.")
        return

    previous = load_state()
    new = current - previous
    if not new:
        print(f"{stamp} No new slots ({len(current)} visible).")
        save_state(current)
        return

    print(f"{stamp} {len(new)} new slot(s):", *sorted(new), sep="\n  ")
    cmd = [sys.executable, "agent.py", "--auto"] + (["--dry-run"] if opts.dry_run else [])
    result = subprocess.run(cmd)
    if result.returncode == 0:
        save_state(current)  # only remember these slots once the agent has handled them
    else:
        print(f"{stamp} agent.py failed (exit {result.returncode}); will retry at the next check.")
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
