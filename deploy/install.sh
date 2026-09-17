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

echo "==> boot-partition copy of the layout choice"
# With the overlay filesystem on (README, "SD-card protection") the state directory lives in
# RAM; the boot partition is the one place root can still write, so settings.json is mirrored
# there by two root-only units. Bookworm mounts it at /boot/firmware, older images at /boot.
# The package default is the Bookworm path; anything else is written into the env file.
DEFAULT_PERSIST_PATH=/boot/firmware/studylife-display/settings.json
PERSIST_PATH=""
if mountpoint -q /boot/firmware; then
  PERSIST_PATH=/boot/firmware/studylife-display/settings.json
elif mountpoint -q /boot; then
  echo "    warning: /boot/firmware is not a mount (older OS?), using /boot instead"
  PERSIST_PATH=/boot/studylife-display/settings.json
else
  echo "    warning: neither /boot/firmware nor /boot is a separate mount; the layout choice"
  echo "    made in the web interface will NOT survive a reboot with the overlay filesystem on"
fi
if [ -n "$PERSIST_PATH" ]; then
  # Plain mkdir: the boot partition is vfat, which has no modes to install -m.
  mkdir -p "$(dirname "$PERSIST_PATH")"
fi

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
# Copy of the layout choice on the boot partition, restored at boot so that it survives the
# overlay filesystem. Empty disables it.
# DISPLAY_PERSIST_PATH=/boot/firmware/studylife-display/settings.json
EOF
    if [ "$PERSIST_PATH" != "$DEFAULT_PERSIST_PATH" ]; then
      printf 'DISPLAY_PERSIST_PATH=%s\n' "$PERSIST_PATH"
    fi
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
  # The only line ever added to an existing env file: a non-default persist path, once.
  if [ "$PERSIST_PATH" != "$DEFAULT_PERSIST_PATH" ] \
     && ! grep -q '^DISPLAY_PERSIST_PATH=' "$ENV_FILE"; then
    echo "    adding DISPLAY_PERSIST_PATH=$PERSIST_PATH (this system's boot partition)"
    printf 'DISPLAY_PERSIST_PATH=%s\n' "$PERSIST_PATH" >> "$ENV_FILE"
  fi
fi

echo "==> systemd units"
install -m 0644 "$SRC/deploy/studylife-display.service" /etc/systemd/system/
install -m 0644 "$SRC/deploy/studylife-display.timer" /etc/systemd/system/
install -m 0644 "$SRC/deploy/studylife-display-web.service" /etc/systemd/system/
install -m 0644 "$SRC/deploy/studylife-display-restore.service" /etc/systemd/system/
install -m 0644 "$SRC/deploy/studylife-display-persist.service" /etc/systemd/system/
install -m 0644 "$SRC/deploy/studylife-display-persist.path" /etc/systemd/system/
systemctl daemon-reload
# Restore first (a stored choice from before this run, e.g. after a reflash), then the units
# that read it. `restart` runs the oneshot again on a re-run; it is a no-op when the state
# directory is already current.
systemctl enable studylife-display-restore.service
systemctl restart studylife-display-restore.service \
  || echo "    note: persist-import failed, see journalctl -u studylife-display-restore.service"
systemctl enable --now studylife-display-persist.path
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
