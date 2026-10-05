"""Browser tools for the Google Calendar booking page, driven by Playwright.

Instead of hard-coding CSS selectors for Google's page (which change often),
the agent reads an accessibility snapshot of the page -- a text outline of
headings, buttons and fields -- and decides what to click. That makes it
much more robust to layout changes.

Safety: the generic click tool refuses to press the final "Book" button.
Booking only happens through confirm_booking, which asks YOU in the terminal
(or, in --auto mode, only after agent.py has checked the booking rules).
"""
import re

from playwright.sync_api import sync_playwright

MAX_SNAPSHOT_CHARS = 20_000
# Names that look like a final booking/submit action must go through confirm_booking.
FINAL_ACTION = re.compile(r"^\s*(book|confirm|reserve)\b", re.IGNORECASE)


class BookingBrowser:
    def __init__(self, url: str, dry_run: bool = False, auto: bool = False,
                 headless: bool = False, profile_dir: str = "browser-profile"):
        self.url = url
        self.dry_run = dry_run
        self.auto = auto  # True: no y/N prompt (rules are checked in agent.py instead)
        self.bookings = []
        self._pw = sync_playwright().start()
        # A persistent profile keeps cookies, so if you ever sign in to Google
        # in this window, you stay signed in on later runs.
        self._ctx = self._pw.chromium.launch_persistent_context(
            profile_dir, headless=headless, slow_mo=150
        )
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        # Show the booking page right away so the window is never left blank.
        self._load()

    def close(self):
        self._ctx.close()
        self._pw.stop()

    # ---- tools exposed to the agent ----

    def open_page(self) -> str:
        self._load()
        return self.snapshot()

    def _load(self):
        # Google pages keep background connections open, so "networkidle" can hang.
        # Wait for the document, then give the slot grid a few seconds to render.
        self.page.goto(self.url, wait_until="domcontentloaded", timeout=60_000)
        try:
            self.page.get_by_role("button").first.wait_for(timeout=15_000)
        except Exception:
            pass  # the snapshot will show whatever did load

    def snapshot(self) -> str:
        self.page.wait_for_timeout(800)  # let dialogs/animations settle
        text = self.page.locator("body").aria_snapshot()
        if len(text) > MAX_SNAPSHOT_CHARS:
            text = text[:MAX_SNAPSHOT_CHARS] + "\n...[snapshot truncated]"
        return text

    def click(self, name: str, role: str = "button", exact: bool = False, nth: int | None = None) -> str:
        if FINAL_ACTION.search(name):
            return "Refused: the final booking button can only be pressed with confirm_booking."
        self._find(role, name, exact, nth).click()
        return self.snapshot()

    def fill(self, label: str, value: str) -> str:
        field = self.page.get_by_label(label)
        if field.count() == 0:
            raise ValueError(f"No field labelled {label!r}. Take a snapshot and check the label.")
        field.first.fill(value)
        return f"Filled {label!r}."

    def confirm_booking(self, slot_description: str, button_name: str = "Book") -> str:
        print("\n" + "=" * 60)
        print(f"  Agent wants to book: {slot_description}")
        print("=" * 60)
        if self.dry_run:
            return (
                "DRY RUN: nothing was booked. Close the booking dialog "
                "(e.g. Cancel/Close) and continue as if this window is handled."
            )
        if not self.auto and input("Book this slot? [y/N] ").strip().lower() != "y":
            return "The user declined this slot. Do not book it; close the dialog and continue."
        self._find("button", button_name, exact=False, nth=None).click()
        self.page.wait_for_timeout(2500)
        self.bookings.append(slot_description)
        return "Booking button clicked. Page now:\n" + self.snapshot()

    # ---- helpers ----

    def _find(self, role, name, exact, nth):
        loc = self.page.get_by_role(role, name=name, exact=exact)
        n = loc.count()
        if n == 0:
            raise ValueError(f"No {role} named {name!r} on the page. Take a snapshot and check the name.")
        if n > 1 and nth is None:
            raise ValueError(f"{n} elements match {role} {name!r}. Pass nth (0-based) or a more specific name.")
        return loc.nth(nth or 0)
