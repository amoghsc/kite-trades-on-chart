#!/bin/bash
# Keep the app running always: starts at login and restarts itself if it ever stops.
# Double-click to install. Run it again with "uninstall" to remove it.
#   ./install-autostart.command uninstall
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.kite-trades-on-chart"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
TARGET="gui/$(id -u)/$LABEL"
PORT="${PORT:-8770}"

if [ "${1:-}" = "uninstall" ]; then
  launchctl bootout "$TARGET" 2>/dev/null
  rm -f "$PLIST"
  echo "Removed. The app no longer starts on its own; use start.command when you want it."
  exit 0
fi

# Pick a real Python. /usr/bin/python3 is a trap here: under launchd it resolves to
# Xcode's stub, which cannot open files outside its own sandbox.
PY=""
for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if [ -x "$c" ] && [ -z "$("$c" -c 'pass' 2>&1)" ] && ! "$c" -c 'import sys;print(sys.executable)' | grep -q Xcode; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Could not find a usable python3. Install it from python.org, then run this again."
  read -r -p "Press return to close." _; exit 1
fi

# Logs go to ~/Library/Logs, never inside the app folder. A log file living under
# Documents can pick up a com.apple.macl access attribute, after which launchd cannot
# open it and refuses to start the job at all, with a bare EX_CONFIG and no explanation.
LOG="$HOME/Library/Logs/kite-trades-on-chart.log"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs" "$HERE/cache"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>              <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$HERE/serve.py</string>
  </array>
  <key>WorkingDirectory</key>   <string>$HERE</string>
  <key>EnvironmentVariables</key><dict><key>PORT</key><string>$PORT</string></dict>
  <key>RunAtLoad</key>          <true/>
  <key>KeepAlive</key>          <true/>
  <key>ProcessType</key>        <string>Background</string>
  <key>StandardOutPath</key>    <string>$LOG</string>
  <key>StandardErrorPath</key>  <string>$LOG</string>
</dict>
</plist>
PLISTEOF

launchctl bootout "$TARGET" 2>/dev/null
if launchctl bootstrap "gui/$(id -u)" "$PLIST"; then
  sleep 2
  if curl -s -o /dev/null --max-time 5 "http://localhost:$PORT/"; then
    echo "Done. The app is running at http://localhost:$PORT and will start automatically from now on."
    open "http://localhost:$PORT" 2>/dev/null
  else
    echo "Installed, but it is not answering yet. Check $LOG"
  fi
else
  echo "Could not install the login agent. You can still use start.command."
fi
read -r -p "Press return to close." _
