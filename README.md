# Booking agent: setup in VS Code

An agent that books slots on a Google Calendar booking page under a personal Gmail account and invites your-name@ucsb.edu to each booking. It runs a Claude tool-use loop in Python.

## How it works

```
you ──request──▶ agent.py ──messages + tools──▶ Claude API
                    ▲                                │
                    │◀──── "call tool X with args" ──┘
                    │
               run_tool() ──▶ Calendar API (busy times; add guests to bookings)
                         └──▶ Playwright browser (booking page)
                    │
                    └──── tool result ──▶ Claude ... (repeat until done)
```

The Calendar API can't book someone else's booking page, so the agent uses a real browser window for that part. You can watch it work. Before each final **Book** click, it stops and asks you `y/N` in the terminal.

## 1. Project setup

1. Install Python 3.10+ and VS Code with the **Python** extension.
2. Put these files in a folder and open it with **File → Open Folder**.
3. Open a terminal with **Terminal → New Terminal**, then run:
   ```bash
   python -m venv .venv
   source .venv/bin/activate          # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   playwright install chromium
   ```
4. When VS Code asks, choose the `.venv` interpreter. You can also pick it later from the Python version shown in the bottom-right corner.
5. Create a `.gitignore` containing:
   ```
   .env
   credentials.json
   token.json
   browser-profile/
   .venv/
   __pycache__/
   bookings.json
   logs/
   ```

## 2. Claude API key

1. Go to console.anthropic.com, sign in, and create an API key. API usage is billed separately from a claude.ai subscription, so add credits under Billing.
2. Copy `.env.example` to `.env`, paste the key in, and fill in your last name.

## 3. Google Calendar API access (personal Gmail)

UCSB doesn't allow enabling the Calendar API, so this uses your personal Gmail account.

1. Go to console.cloud.google.com while signed in as **your-name@gmail.com**, and create a project.
2. Under **APIs & Services → Library**, enable **Google Calendar API** and **Gmail API**. Gmail is only used to send the booking notification; the app can send mail but not read your inbox.
3. Under **OAuth consent screen**, choose **External**, fill in the app name and your email, and add your-name@gmail.com as a **test user**.
4. Under **Credentials → Create credentials → OAuth client ID**, choose **Desktop app**. Download the JSON and save it in this folder as `credentials.json`.
5. On the first run, a browser opens for you to sign in with Gmail and approve access. You may see "Google hasn't verified this app": click **Continue**, since it's your own app. That saves `token.json`.

While the app is in Testing mode, Google expires the sign-in after 7 days. For runs by hand, the code notices and opens the sign-in page again. For daily automatic runs, see section 5.

### Optional: check your UCSB calendar for conflicts too

The agent can only see calendars your Gmail account can see. To include your UCSB schedule:

1. Open Google Calendar as **your-name@ucsb.edu** → **Settings** → your calendar → **Share with specific people**.
2. Add your-name@gmail.com with **See only free/busy**.

If UCSB doesn't allow sharing outside the university, remove `your-name@ucsb.edu` from `BUSY_CALENDARS` in `.env`. The agent then checks only your Gmail calendar.

### Booking notifications

After every real booking (not dry runs), your Gmail sends an email to `NOTIFY_EMAIL` (your-name@ucsb.edu) with the slot details. This comes from the code itself, so it doesn't depend on Claude remembering. If sending fails, the booking still stands and the terminal shows why.

### How your-name@ucsb.edu gets invited

If the booking form has a guest field, the agent enters your-name@ucsb.edu there. Otherwise, after each booking, it finds the event on your Gmail calendar and adds your-name@ucsb.edu as a guest. This only works if the schedule owner allows guests to invite others. If they don't, the agent tells you, and you can forward the invitation by hand.

## 4. Run it

Always do a dry run first. It goes through every step except the final booking click:

```bash
python agent.py --dry-run "Book one slot every 2 weeks on any weekday, between 11:00am and 1:00pm, from Oct 5 to Nov 30."
```

Watch the browser and the terminal output. If it looks right, run without `--dry-run` and answer `y` or `n` at each booking.

## 5. Run it automatically every day (Mac)

In automatic mode (`--auto`) nobody approves bookings, so the code checks every slot against your rules before it is booked. Claude picks the slot; the code can refuse it. A slot is booked only if:

- the whole meeting fits inside `AUTO_TIME_WINDOW` (e.g. 11:00–13:00),
- its date is between `AUTO_START_DATE` and `AUTO_END_DATE`,
- its window (every `AUTO_INTERVAL_DAYS` days from the start date) has no booking yet,
- it doesn't clash with anything on `BUSY_CALENDARS`, and every one of those calendars could be read.

Every booking, by hand or automatic, is recorded in `bookings.json`. That's how later runs know a window is done. Once every window is booked or has passed, runs stop immediately without calling the Claude API.

### Step 1: keep Google from signing you out every 7 days

Apps in Testing mode lose their Google sign-in after 7 days, which would break the daily runs. In Google Cloud, go to **Google Auth Platform → Audience** (or **OAuth consent screen**) and click **Publish app** to switch to **In production**. You don't need to submit it for verification. It stays private to your account, and the sign-in page keeps showing the "unverified app" warning, which is fine. Then delete `token.json` and run a dry run once to sign in again.

### Step 2: set the rules and test them

Add the `AUTO_...` lines from `.env.example` to your `.env`, then run a test:

```bash
python agent.py --auto --dry-run
```

The terminal shows `[rules]` lines, saying whether each slot Claude tried passed or was refused and why.

### Step 3: install the daily schedule

```bash
chmod +x schedule_mac.sh
./schedule_mac.sh install 7 5     # every day at 7:05am; change the hour and minute as you like
./schedule_mac.sh test            # run it once right now
tail -f logs/auto.log             # watch the output (Ctrl+C stops watching, not the job)
```

If the Mac is asleep at 7:05, the run happens when it wakes. If it's shut down, that day is skipped. A browser window pops up during each run; set `HEADLESS=true` in `.env` to hide it. If you hide it and runs start failing, set it back, since Google sometimes treats hidden browsers differently.

To stop the daily runs: `./schedule_mac.sh uninstall`. To check on them: `./schedule_mac.sh status`.

### What you'll get

- **A booking:** an email to `NOTIFY_EMAIL`, plus the calendar invite for your-name@ucsb.edu.
- **A failed run** (for example, an expired API key or Google sign-in): an email titled "Booking agent: automatic run failed". Fix the problem, then run `python agent.py --auto --dry-run` by hand to confirm.
- **Nothing available:** no email. Check `logs/auto.log` if you want to see what it looked at.

Each run costs a small amount of API credit. Check **Usage** in the Claude Console after a few days to see what it adds up to. Your API key's expiry date also applies here; renew it in `.env` before it runs out.

## Troubleshooting

- **`logs/auto.log` says "Operation not permitted".** macOS blocks background jobs from reading Documents, Desktop and Downloads. Either move the project folder somewhere like `~/Projects/meeting_booking` (then delete and recreate `.venv`, and run `install` again), or give Python access: **System Settings → Privacy & Security → Full Disk Access → +**, press **Cmd+Shift+G**, and enter `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14`.

- **The booking form asks for something the agent doesn't have.** The agent stops and tells you. Add the detail to your request, or to `.env` and the system prompt.
- **The page needs you signed in to Google.** Sign in once in the Playwright window, and the `browser-profile/` folder remembers it. Google sometimes blocks sign-in from automated browsers. If that happens, try `channel="chrome"` in `launch_persistent_context` (this uses your installed Chrome).
- **The agent gets lost on the page.** Look at what `snapshot_page` returns. The fix is usually a clearer instruction in `system_prompt()` about how this particular page labels its slots.
- **You want it to also create events**, for example prep reminders. The `calendar.events` scope already allows this, so you only need to add a `create_event` tool.
- **"invalid_grant" or sign-in errors after changing scopes.** Delete `token.json` and run again.
