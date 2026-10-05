"""One Google sign-in shared by the Calendar and Gmail tools (personal Gmail account)."""
import os

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",    # read events + add guests
    "https://www.googleapis.com/auth/calendar.freebusy",  # busy times, incl. shared calendars
    "https://www.googleapis.com/auth/gmail.send",         # booking notification emails (send only)
]

_creds = None


def _sign_in():
    flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
    # login_hint pre-selects the personal Gmail account in the Google sign-in screen.
    return flow.run_local_server(port=0, login_hint=os.getenv("BOOKER_EMAIL", ""))


class SignInNeeded(RuntimeError):
    pass


def get_credentials(interactive: bool = True):
    """Return valid Google credentials, signing in again when needed.

    With interactive=False (scheduled runs), raise SignInNeeded instead of
    opening a sign-in page that nobody is there to click.
    """
    global _creds
    if _creds is not None and _creds.valid:
        return _creds

    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json")
        if not creds.has_scopes(SCOPES):
            creds = None  # token predates a scope change (e.g. Gmail added): sign in again

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                # Google expires tokens for apps in "Testing" mode after 7 days.
                creds = None
        if not creds or not creds.valid:
            if not interactive:
                raise SignInNeeded(
                    "Google sign-in has expired. Run `python agent.py --dry-run --auto` "
                    "once by hand to sign in again."
                )
            creds = _sign_in()
        with open("token.json", "w") as f:
            f.write(creds.to_json())

    _creds = creds
    return _creds
