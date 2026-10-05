#!/bin/bash
# Run the booking agent automatically every day on this Mac (launchd).
#
#   ./schedule_mac.sh install 7 5   # every day at 7:05am (hour, minute; default 7:05)
#   ./schedule_mac.sh test          # run the scheduled job right now
#   ./schedule_mac.sh status        # is it installed? when did it last run?
#   ./schedule_mac.sh uninstall     # stop the daily runs
#
# If the Mac is asleep at the scheduled time, macOS runs the job when it wakes.
# If the Mac is shut down, that day's run is skipped.
set -euo pipefail

LABEL="com.summer.bookingagent"
PROJECT="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON="$PROJECT/.venv/bin/python"
DOMAIN="gui/$(id -u)"

case "${1:-}" in
  install)
    HOUR="${2:-7}"
    MINUTE="${3:-5}"
    [ -x "$PYTHON" ] || { echo "No .venv found in $PROJECT. Create it first (see README)."; exit 1; }
    [ -f "$PROJECT/.env" ] || { echo "No .env found in $PROJECT."; exit 1; }
    [ -f "$PROJECT/token.json" ] || { echo "No token.json yet. Run 'python agent.py --auto --dry-run' once by hand first."; exit 1; }

    case "$PROJECT" in
      "$HOME/Documents"*|"$HOME/Desktop"*|"$HOME/Downloads"*)
        echo "Note: this folder is inside Documents/Desktop/Downloads, which macOS protects."
        echo "      If the test run's log says 'Operation not permitted', see README (Full Disk Access)."
        ;;
    esac

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
    <string>$PROJECT/agent.py</string>
    <string>--auto</string>
  </array>
  <key>WorkingDirectory</key><string>$PROJECT</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>$HOUR</integer>
    <key>Minute</key><integer>$MINUTE</integer>
  </dict>
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
    printf "Installed: runs every day at %d:%02d. Log: %s/logs/auto.log\n" "$HOUR" "$MINUTE" "$PROJECT"
    ;;

  test)
    launchctl kickstart -p "$DOMAIN/$LABEL"
    echo "Started. Follow the log with:  tail -f \"$PROJECT/logs/auto.log\"   (Ctrl+C to stop watching)"
    ;;

  status)
    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
      echo "Installed."
      launchctl print "$DOMAIN/$LABEL" | grep -E "state|last exit code|runs" || true
    else
      echo "Not installed."
    fi
    ;;

  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Uninstalled. Daily runs stopped."
    ;;

  *)
    sed -n '2,10p' "$0"
    exit 1
    ;;
esac
