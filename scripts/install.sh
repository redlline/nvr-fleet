#!/usr/bin/env bash
# NVR Fleet Agent installer
# Usage: curl -fsSL http(s)://SERVER/install.sh | bash -s -- --site SITE_ID --token TOKEN --server HOST --scheme http|https
set -euo pipefail

SITE_ID=""
TOKEN=""
SERVER=""
SCHEME="https"

while [[ $# -gt 0 ]]; do
  case $1 in
    --site)   SITE_ID="$2"; shift 2 ;;
    --token)  TOKEN="$2";   shift 2 ;;
    --server) SERVER="$2";  shift 2 ;;
    --scheme) SCHEME="$2";  shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

[[ -z "$SITE_ID" || -z "$TOKEN" || -z "$SERVER" ]] && {
  echo "Usage: install.sh --site SITE_ID --token TOKEN --server HOST --scheme http|https"
  exit 1
}
[[ "$SCHEME" != "http" && "$SCHEME" != "https" ]] && {
  echo "Unsupported scheme: $SCHEME"
  exit 1
}
if [[ "$SCHEME" == "https" ]]; then
  WS_SCHEME="wss"
else
  WS_SCHEME="ws"
fi

ARCH=$(uname -m)
case "$ARCH" in
  x86_64)  GO2RTC_ARCH="amd64" ;;
  aarch64) GO2RTC_ARCH="arm64" ;;
  armv7l)  GO2RTC_ARCH="arm"   ;;
  *)       echo "Unsupported arch: $ARCH"; exit 1 ;;
esac

echo "=== NVR Fleet Agent installer ==="
echo "Site:   $SITE_ID"
echo "Server: $SERVER"
echo "Scheme: $SCHEME"
echo "Arch:   $ARCH ($GO2RTC_ARCH)"
echo

AGENT_DIR="/opt/nvr-fleet-agent"
VENV_DIR="${AGENT_DIR}/.venv"

# --- Dependencies ---
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv ffmpeg curl

# --- Python runtime ---
echo "Preparing Python virtual environment..."
mkdir -p "$AGENT_DIR"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip setuptools wheel
"$VENV_DIR/bin/pip" install --quiet websockets pyyaml fastapi uvicorn

# --- go2rtc ---
echo "Installing go2rtc..."
GO2RTC_URL="https://github.com/AlexxIT/go2rtc/releases/latest/download/go2rtc_linux_${GO2RTC_ARCH}"
curl -fsSL "$GO2RTC_URL" -o /usr/local/bin/go2rtc
chmod +x /usr/local/bin/go2rtc
mkdir -p /etc/go2rtc

# --- Agent ---
echo "Installing fleet-agent..."
curl -fsSL "${SCHEME}://${SERVER}/agent/agent.py" -o "${AGENT_DIR}/agent.py"

# --- Environment ---
cat > /etc/nvr-fleet-agent.env << EOF
SITE_ID=${SITE_ID}
AGENT_TOKEN=${TOKEN}
SERVER_HOST=${SERVER}
SERVER_WS=${WS_SCHEME}://${SERVER}/ws/agent/${SITE_ID}
SERVER_API=${SCHEME}://${SERVER}
SERVER_RTSP_PORT=8554
GO2RTC_BIN=/usr/local/bin/go2rtc
GO2RTC_YAML=/etc/go2rtc/go2rtc.yaml
GO2RTC_SVC=go2rtc
FFMPEG_BIN=/usr/bin/ffmpeg
AGENT_ADMIN_HOST=0.0.0.0
AGENT_ADMIN_PORT=7070
EOF
chmod 600 /etc/nvr-fleet-agent.env

# --- go2rtc systemd ---
cat > /etc/systemd/system/go2rtc.service << 'EOF'
[Unit]
Description=go2rtc NVR agent
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
ExecStart=/usr/local/bin/go2rtc -config /etc/go2rtc/go2rtc.yaml
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# --- fleet-agent systemd ---
cat > /etc/systemd/system/nvr-fleet-agent.service << 'EOF'
[Unit]
Description=NVR Fleet Agent
After=network-online.target go2rtc.service
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
EnvironmentFile=/etc/nvr-fleet-agent.env
ExecStart=/opt/nvr-fleet-agent/.venv/bin/python /opt/nvr-fleet-agent/agent.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now go2rtc
systemctl enable --now nvr-fleet-agent

echo
echo "=== Installation complete ==="
echo "Agent status: $(systemctl is-active nvr-fleet-agent)"
echo "go2rtc status: $(systemctl is-active go2rtc)"
echo "Check logs: journalctl -u nvr-fleet-agent -f"
echo "Local admin: http://$(hostname -I | awk '{print $1}'):7070"
