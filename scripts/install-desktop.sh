#!/usr/bin/env sh
set -eu

SERVICE_NAME="rsap-desktop-backend"
APP_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)/apps/desktop-backend"
LOG_DIR="$HOME/.rsap/logs"
mkdir -p "$LOG_DIR"

case "$(uname -s)" in
  Linux)
    if ! command -v systemctl >/dev/null 2>&1; then
      echo "systemctl is required to install the Linux service" >&2
      exit 1
    fi
    UNIT="$HOME/.config/systemd/user/${SERVICE_NAME}.service"
    mkdir -p "$(dirname "$UNIT")"
    cat > "$UNIT" <<EOF
[Unit]
Description=RSAP desktop backend
After=network-online.target

[Service]
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8001
Restart=on-failure
RestartSec=5
StandardOutput=append:$LOG_DIR/backend.log
StandardError=append:$LOG_DIR/backend.err.log

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now "$SERVICE_NAME"
    echo "Installed $SERVICE_NAME as a user systemd service"
    ;;
  Darwin)
    PLIST="$HOME/Library/LaunchAgents/com.rsap.desktop-backend.plist"
    mkdir -p "$(dirname "$PLIST")"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.rsap.desktop-backend</string>
  <key>WorkingDirectory</key><string>$APP_DIR</string>
  <key>ProgramArguments</key>
  <array>
    <string>$APP_DIR/.venv/bin/python</string>
    <string>-m</string>
    <string>uvicorn</string>
    <string>main:app</string>
    <string>--host</string>
    <string>127.0.0.1</string>
    <string>--port</string>
    <string>8001</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/backend.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/backend.err.log</string>
</dict>
</plist>
EOF
    launchctl unload "$PLIST" >/dev/null 2>&1 || true
    launchctl load "$PLIST"
    echo "Installed com.rsap.desktop-backend LaunchAgent"
    ;;
  MINGW*|MSYS*|CYGWIN*)
    echo "Use PowerShell on Windows: scripts/install-desktop-service.ps1 is not included yet." >&2
    exit 1
    ;;
  *)
    echo "Unsupported OS: $(uname -s)" >&2
    exit 1
    ;;
esac
