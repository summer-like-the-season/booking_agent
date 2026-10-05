#!/bin/bash
# Run the booking agent automatically on this Mac (launchd). Pick ONE mode:
#
#   ./schedule_mac.sh watch 10       # check for new slots every 10 minutes; run the agent only when new ones appear
#   ./schedule_mac.sh install 7:05 17:05   # or: run the agent every day at these times (24-hour)
#
#   ./schedule_mac.sh test           # run the installed job right now
#   ./schedule_mac.sh status         # is it installed? how did the last run go?
#   ./schedule_mac.sh uninstall      # stop all automatic runs
#
# Installing one mode replaces the other. If the Mac is asleep at a scheduled
# time, macOS runs the job when it wakes; while it's shut down, nothing runs.
set -euo pipefail

LABEL="com.summer.bookingagent"
PROJECT="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON="$PROJECT/.venv/bin/python"
DOMAIN="gui/$(id -u)"

preflight() {
  [ -x "$PYTHON" ] || { echo "No .venv found in $PROJECT. Create it first (see README)."; exit 1; }
  [ -f "$PROJECT/.env" ] || { echo "No .env found in $PROJECT."; exit 1; }
  [ -f "$PROJECT/token.json" ] || { echo "No token.json yet. Run 'python google_auth.py' first to sign in to Google."; exit 1; }
  case "$PROJECT" in
    "$HOME/Documents"*|"$HOME/Desktop"*|"$HOME/Downloads"*)
      echo "Note: this folder is inside Documents/Desktop/Downloads, which macOS protects."
      echo "      If the test run's log says 'Operation not permitted', see README."
      ;;
  esac
}

# write_plist <script> <schedule-xml>
write_plist() {
  mkdir -p "$PROJECT/logs" "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
$1
  </array>
  <key>WorkingDirectory</key><string>$PROJECT</string>
$2
  <key>StandardOutPath</key><string>$PROJECT/logs/auto.log</string>
  <key>StandardErrorPath</key><string>$PROJECT/logs/auto.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
</dict>
</plist>
EOF
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$PLIST"
}

case "${1:-}" in
  watch)
    MINUTES="${2:-10}"
    if ! [[ "$MINUTES" =~ ^[0-9]+$ ]] || [ "$MINUTES" -lt 5 ]; then
      echo "Use a whole number of minutes, at least 5 (e.g. ./schedule_mac.sh watch 10)."; exit 1
    fi
    preflight
    write_plist "    <string>$PROJECT/watch.py</string>" \
      "  <key>StartInterval</key><integer>$((MINUTES * 60))</integer>
  <key>RunAtLoad</key><true/>"
    echo "Installed: checks for new slots every $MINUTES minutes. Log: $PROJECT/logs/auto.log"
    ;;

  install)
    shift
    # (written this way so it also works with the older bash that ships with macOS)
    if [ $# -gt 0 ]; then TIMES=("$@"); else TIMES=("7:05"); fi
    INTERVALS=""
    for T in "${TIMES[@]}"; do
      if ! [[ "$T" =~ ^([01]?[0-9]|2[0-3]):([0-5][0-9])$ ]]; then
        echo "Bad time '$T'. Use 24-hour HH:MM, e.g. 7:05 or 17:30."; exit 1
      fi
      H=$((10#${BASH_REMATCH[1]})); M=$((10#${BASH_REMATCH[2]}))
      INTERVALS+="    <dict><key>Hour</key><integer>$H</integer><key>Minute</key><integer>$M</integer></dict>
"
    done
    preflight
    write_plist "    <string>$PROJECT/agent.py</string>
    <string>--auto</string>" \
      "  <key>StartCalendarInterval</key>
  <array>
$INTERVALS  </array>"
    echo "Installed: runs every day at ${TIMES[*]}. Log: $PROJECT/logs/auto.log"
    ;;

  test)
    launchctl kickstart -p "$DOMAIN/$LABEL"
    echo "Started. Follow the log with:  tail -f \"$PROJECT/logs/auto.log\"   (Ctrl+C to stop watching)"
    ;;

  status)
    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
      if grep -q "watch.py" "$PLIST" 2>/dev/null; then echo "Installed (watch mode)."; else echo "Installed (daily times)."; fi
      launchctl print "$DOMAIN/$LABEL" | grep -E "state|last exit code|runs" || true
    else
      echo "Not installed."
    fi
    ;;

  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Uninstalled. Automatic runs stopped."
    ;;

  *)
    sed -n '2,13p' "$0"
    exit 1
    ;;
esac
