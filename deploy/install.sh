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

suggest_token() {
  # 32 hex characters from the kernel's random source; openssl is usually there, od always.
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 16
  else
    head -c 16 /dev/urandom | od -An -tx1 | tr -d ' 
'
  fi
}

if [ ! -f "$ENV_FILE" ]; then
  echo "==> writing template $ENV_FILE (fill in the URL and key!)"
  # The web interface's access token is chosen by the person installing, never by the
  # code: suggest a random one, let Enter accept it or a typed value replace it, and never
  # print the final value back (it goes into the root-owned env file only).
  SUGGESTED="$(suggest_token)"
  WEB_TOKEN=""
  if [ -t 0 ]; then
    echo
    echo "The web interface (http://$(hostname).local:8795/) asks for an access token."
    echo "Press Enter to use the suggested one, or type your own (at least 12 characters):"
    echo "    suggested: $SUGGESTED"
    while :; do
      printf 'DISPLAY_WEB_TOKEN [Enter = suggested]: '
      IFS= read -r WEB_TOKEN
      WEB_TOKEN="${WEB_TOKEN:-$SUGGESTED}"
      if [ "${#WEB_TOKEN}" -ge 12 ]; then
        break
      fi
      echo "too short - at least 12 characters, please"
    done
  else
    # No terminal (unattended install): take the suggestion; it is in the env file.
    WEB_TOKEN="$SUGGESTED"
  fi
  {
    cat <<'EOF'
# StudyLife instance and a READ-ONLY API key with the scopes
# Metrics.GetSummary, Sessions.GetHistory, TimerState.Get.
STUDYLIFE_BASE_URL=https://studylife.example.com
STUDYLIFE_API_KEY=replace-me
# Time zone of the StudyLife SERVER (its timestamps carry no offset).
STUDYLIFE_TIMEZONE=Europe/Berlin
# DISPLAY_LANGUAGE=de
# Layout when the web interface has not chosen one yet: auto, classic, focus, exam, week.
# DISPLAY_LAYOUT=auto
# Web interface: bind address and the access token asked for on its login page.
# DISPLAY_WEB_BIND=0.0.0.0:8795
EOF
    printf 'DISPLAY_WEB_TOKEN=%s
' "$WEB_TOKEN"
  } > "$ENV_FILE"
  unset WEB_TOKEN SUGGESTED
  chown root:"$SERVICE_USER" "$ENV_FILE"
  chmod 0640 "$ENV_FILE"
else
  echo "==> keeping existing $ENV_FILE"
  if ! grep -q '^DISPLAY_WEB_TOKEN=' "$ENV_FILE"; then
    echo "    note: it has no DISPLAY_WEB_TOKEN yet - the web interface will refuse to start"
    echo "    until you add one (at least 12 characters), e.g. DISPLAY_WEB_TOKEN=$(suggest_token)"
  fi
fi

echo "==> systemd units"
install -m 0644 "$SRC/deploy/studylife-display.service" /etc/systemd/system/
install -m 0644 "$SRC/deploy/studylife-display.timer" /etc/systemd/system/
install -m 0644 "$SRC/deploy/studylife-display-web.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now studylife-display.timer
systemctl enable --now studylife-display-web.service
# A re-run has just reinstalled the package: pick the new code up right away.
systemctl restart studylife-display-web.service || true

cat <<EOF

Installed. Next steps:
  1. sudo nano $ENV_FILE            # URL + API key
  2. sudo systemctl start studylife-display.service
  3. journalctl -u studylife-display.service -n 50
The timer refreshes the panel every 5 minutes: systemctl list-timers studylife-display.timer
Layouts are switched in the web interface:  http://$(hostname).local:8795/
  (journalctl -u studylife-display-web.service -n 50 if it does not answer)
EOF
