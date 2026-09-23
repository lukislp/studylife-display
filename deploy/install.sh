#!/usr/bin/env bash
# Installs studylife-display on a Raspberry Pi (Raspberry Pi OS Lite, Bookworm or newer).
#
# Run as the `pi` user (or any sudo-capable user; the systemd unit runs as `pi`):
#
#   git clone https://github.com/lukislp/studylife-display.git
#   sudo bash studylife-display/deploy/install.sh
#
# Re-running it updates the checkout under /opt/studylife-display/src and reinstalls the
# package; the environment file and the cached snapshot are left alone. The checkout is the
# latest release tag; `--main` tracks origin/main instead (for developers). Later updates:
# deploy/update.sh.
set -euo pipefail

TRACK_MAIN=0
for arg in "$@"; do
  case "$arg" in
    --main) TRACK_MAIN=1 ;;
    -h|--help)
      echo "usage: sudo bash $0 [--main]"
      echo "  --main  check out origin/main instead of the latest release tag"
      exit 0
      ;;
    *)
      echo "unknown argument: $arg (usage: sudo bash $0 [--main])" >&2
      exit 1
      ;;
  esac
done

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

echo "==> system packages (Pillow runtime, git, venv, lgpio build/runtime)"
apt-get update
# swig and liblgpio-dev are needed to build the `lgpio` Python package's C extension (it has
# no prebuilt wheel for this platform); liblgpio-dev pulls in the liblgpio1 runtime library.
apt-get install -y python3-venv python3-pip git libopenjp2-7 fonts-dejavu-core swig liblgpio-dev

echo "==> enabling SPI (the HAT is driven over SPI0)"
raspi-config nonint do_spi 0

echo "==> source checkout at $SRC"
mkdir -p "$PREFIX"
if [ ! -d "$SRC/.git" ]; then
  if [ -d "$HERE/.git" ]; then
    # Installing from a checkout somewhere else (the usual first run): copy it into place.
    git clone "$HERE" "$SRC"
    git -C "$SRC" remote set-url origin "$REPO_URL"
  else
    git clone "$REPO_URL" "$SRC"
  fi
fi
# The tags come from GitHub, not from the local copy (which may be shallow or untagged).
git -C "$SRC" fetch --tags --prune origin
if [ "$TRACK_MAIN" -eq 1 ]; then
  echo "    tracking origin/main (--main)"
  git -C "$SRC" checkout --force --quiet -B main origin/main
else
  # The newest vX.Y.Z tag; the version the package reports comes from it (hatch-vcs).
  RELEASE_TAG="$(git -C "$SRC" tag --list 'v*' --sort=-version:refname | head -n 1)"
  if [ -z "$RELEASE_TAG" ]; then
    echo "    warning: no release tag found, using origin/main"
    git -C "$SRC" checkout --force --quiet -B main origin/main
  else
    echo "    release $RELEASE_TAG"
    git -C "$SRC" checkout --force --detach --quiet "$RELEASE_TAG"
  fi
fi

echo "==> virtualenv at $VENV"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --upgrade pip
# The `pi` extra pulls the Waveshare library straight from its git repository plus the
# spidev/gpiozero/lgpio bindings; lgpio compiles against the system headers, hence
# python3-pip/venv above. Everything else comes as prebuilt wheels from piwheels.
# pip's git clone (and its build tmp dirs) land in TMPDIR/$TMPDIR by default, i.e. /tmp - a
# tmpfs sized from RAM. On a 512 MB board (Pi 3 A+) that is far smaller than the Waveshare
# e-Paper repo, so the clone fails part-way with "unable to write file" for unrelated files.
# Point it at the real disk instead; $PREFIX is created above and has room to spare.
mkdir -p "$PREFIX/tmp"
TMPDIR="$PREFIX/tmp" "$VENV/bin/pip" install --upgrade "$SRC[pi]"
rm -rf "$PREFIX/tmp"

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
  echo "==> writing template $ENV_FILE (fill in the key!)"
  PLACEHOLDER_URL="https://studylife.example.com"
  STUDYLIFE_URL="$PLACEHOLDER_URL"
  if [ -t 0 ]; then
    echo
    echo "StudyLife instance URL (the server this display reads from):"
    while :; do
      printf 'STUDYLIFE_BASE_URL [e.g. https://studylife.example.com]: '
      IFS= read -r STUDYLIFE_URL
      case "$STUDYLIFE_URL" in
        http://*|https://*) break ;;
        *) echo "needs to start with http:// or https://, please" ;;
      esac
    done
  else
    # No terminal (unattended install): leave the placeholder; edit the env file afterwards.
    echo "    no terminal attached, leaving STUDYLIFE_BASE_URL as a placeholder"
  fi
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
# StudyLife instance. The READ-ONLY API key (scopes Metrics.GetSummary, Sessions.GetAll,
# Sessions.GetHistory, TimerState.Get) is filled in by the web interface's connect page
# (http://<hostname>.local:8795/connect); pasting one here by hand works too.
EOF
    printf 'STUDYLIFE_BASE_URL=%s\n' "$STUDYLIFE_URL"
    cat <<'EOF'
STUDYLIFE_API_KEY=
# Time zone of the StudyLife SERVER (its timestamps carry no offset).
STUDYLIFE_TIMEZONE=Europe/Berlin
# DISPLAY_LANGUAGE=de
# Layout when the web interface has not chosen one yet: auto, classic, focus, exam, week,
# semester, agenda, review.
# DISPLAY_LAYOUT=auto
# Windows of two auto rules ([weekdays] HH-HH, 24 = midnight; empty = rule off): the weekly
# review, tried first, and the agenda, tried last before classic.
# DISPLAY_AUTO_REVIEW=sun 18-24
# DISPLAY_AUTO_AGENDA=06-12
# Web interface: bind address and the access token asked for on its login page.
# DISPLAY_WEB_BIND=0.0.0.0:8795
# Optional https URL of this web interface (a Tailscale name, say): StudyLife then
# redirects straight back to <url>/connect/callback when connecting the account.
# DISPLAY_PUBLIC_BASE_URL=
# Optional exact URL for the setup screen's QR code; empty derives it from the hostname
# (http://<hostname>.local:8795/connect) or from DISPLAY_PUBLIC_BASE_URL.
# DISPLAY_SETUP_URL=
# Copy of the layout choice on the boot partition, restored at boot so that it survives the
# overlay filesystem. Empty disables it.
# DISPLAY_PERSIST_PATH=/boot/firmware/studylife-display/settings.json
# Panel mounted upside down: 180 (default 0).
# DISPLAY_ROTATE=0
# Quiet hours (HH-HH or HH:MM-HH:MM, may wrap past midnight): no scheduled refresh inside.
# DISPLAY_QUIET_HOURS=23-7
# One full clear per day against ghosting, at this time (empty = off).
# DISPLAY_CLEAR_AT=04:00
# Show the "data is stale" screen once the cached snapshot is older than this many hours.
# DISPLAY_STALE_ERROR_HOURS=24
# Let the web interface ask GitHub (once per 6 h) whether a newer release exists.
# DISPLAY_UPDATE_CHECK=false
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
install -m 0644 "$SRC/deploy/studylife-display-credentials.service" /etc/systemd/system/
install -m 0644 "$SRC/deploy/studylife-display-credentials.path" /etc/systemd/system/
systemctl daemon-reload
# Restore first (a stored choice from before this run, e.g. after a reflash), then the units
# that read it. `restart` runs the oneshot again on a re-run; it is a no-op when the state
# directory is already current.
systemctl enable studylife-display-restore.service
systemctl restart studylife-display-restore.service \
  || echo "    note: persist-import failed, see journalctl -u studylife-display-restore.service"
systemctl enable --now studylife-display-persist.path
systemctl enable --now studylife-display-credentials.path
systemctl enable --now studylife-display.timer
systemctl enable --now studylife-display-web.service
# A re-run has just reinstalled the package: pick the new code up right away.
systemctl restart studylife-display-web.service || true

# The URL prompt above already wrote a real STUDYLIFE_BASE_URL for a fresh, interactive
# install; anything else (unattended install, or a pre-existing env file) still has the
# placeholder and needs the manual-edit step spelled out.
if grep -q '^STUDYLIFE_BASE_URL=https://studylife\.example\.com$' "$ENV_FILE" 2>/dev/null; then
  cat <<EOF

Installed. Next steps:
  1. sudo nano $ENV_FILE            # STUDYLIFE_BASE_URL
  2. sudo systemctl restart studylife-display-web.service
  3. http://$(hostname).local:8795/connect   # connect the account (no key to copy);
     the panel shows this URL as a QR code until a key is applied
     or put the key into $ENV_FILE by hand and run
     sudo systemctl start studylife-display.service
  4. journalctl -u studylife-display.service -n 50
EOF
else
  cat <<EOF

Installed. Next steps:
  1. http://$(hostname).local:8795/connect   # connect the account (no key to copy);
     the panel shows this URL as a QR code until a key is applied
     or put the key into $ENV_FILE by hand and run
     sudo systemctl start studylife-display.service
  2. journalctl -u studylife-display.service -n 50
EOF
fi
cat <<EOF
The timer refreshes the panel every 5 minutes: systemctl list-timers studylife-display.timer
Layouts are switched in the web interface:  http://$(hostname).local:8795/
  (journalctl -u studylife-display-web.service -n 50 if it does not answer)
Health for an uptime monitor:               http://$(hostname).local:8795/healthz
Later updates:                              sudo bash $SRC/deploy/update.sh [--check]
EOF
