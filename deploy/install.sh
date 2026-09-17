#!/usr/bin/env bash
# Installs studylife-display on a Raspberry Pi (Raspberry Pi OS Lite, Bookworm or newer).
#
# Run as the `pi` user (or any sudo-capable user; the systemd unit runs as `pi`):
#
#   git clone https://github.com/lukislp/studylife-display.git
#   sudo bash studylife-display/deploy/install.sh
#
# Re-running it updates the checkout under /opt/studylife-display/src and reinstalls the
# package; the environment file and the cached snapshot are left alone.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/lukislp/studylife-display.git}"
PREFIX=/opt/studylife-display
SRC="$PREFIX/src"
VENV="$PREFIX/venv"
ENV_FILE=/etc/studylife-display.env
STATE_DIR=/var/lib/studylife-display
SERVICE_USER="${SERVICE_USER:-pi}"

if [ "$(id -u)" -ne 0 ]; then
  echo "run with sudo: sudo bash $0" >&2
  exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> system packages (Pillow runtime, git, venv)"
apt-get update
apt-get install -y python3-venv python3-pip git libopenjp2-7 fonts-dejavu-core

echo "==> enabling SPI (the HAT is driven over SPI0)"
raspi-config nonint do_spi 0

echo "==> source checkout at $SRC"
mkdir -p "$PREFIX"
if [ -d "$SRC/.git" ]; then
  git -C "$SRC" fetch --tags --prune
  git -C "$SRC" reset --hard origin/main
elif [ -d "$HERE/.git" ]; then
  # Installing from a checkout somewhere else (the usual first run): copy it into place.
  git clone "$HERE" "$SRC"
  git -C "$SRC" remote set-url origin "$REPO_URL"
else
  git clone "$REPO_URL" "$SRC"
fi

echo "==> virtualenv at $VENV"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --upgrade pip
# The `pi` extra pulls the Waveshare library straight from its git repository plus the
# spidev/gpiozero/lgpio bindings; lgpio compiles against the system headers, hence
# python3-pip/venv above. Everything else comes as prebuilt wheels from piwheels.
"$VENV/bin/pip" install --upgrade "$SRC[pi]"

echo "==> state directory $STATE_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$STATE_DIR"

if [ ! -f "$ENV_FILE" ]; then
  echo "==> writing template $ENV_FILE (fill in the URL and key!)"
  cat > "$ENV_FILE" <<'EOF'
# StudyLife instance and a READ-ONLY API key with the scopes
# Metrics.GetSummary, Sessions.GetHistory, TimerState.Get.
STUDYLIFE_BASE_URL=https://studylife.example.com
STUDYLIFE_API_KEY=replace-me
# Time zone of the StudyLife SERVER (its timestamps carry no offset).
STUDYLIFE_TIMEZONE=Europe/Berlin
# DISPLAY_LANGUAGE=de
EOF
  chown root:"$SERVICE_USER" "$ENV_FILE"
  chmod 0640 "$ENV_FILE"
else
  echo "==> keeping existing $ENV_FILE"
fi

echo "==> systemd units"
install -m 0644 "$SRC/deploy/studylife-display.service" /etc/systemd/system/
install -m 0644 "$SRC/deploy/studylife-display.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now studylife-display.timer

cat <<EOF

Installed. Next steps:
  1. sudo nano $ENV_FILE            # URL + API key
  2. sudo systemctl start studylife-display.service
  3. journalctl -u studylife-display.service -n 50
The timer refreshes the panel every 5 minutes: systemctl list-timers studylife-display.timer
EOF
