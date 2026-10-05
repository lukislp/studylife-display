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
#
# `--image` is the mode the SD-card image build (image/build-image.sh) runs inside a chroot
# of the stock Raspberry Pi OS image: nothing is started, SPI is enabled in config.txt, and
# everything that must differ per device (TLS certificate, web token, mDNS id) is left to
# the first-boot unit instead of being baked into the image. See image/README.md.
set -euo pipefail

USAGE="usage: sudo bash $0 [--main] [--panel KEY] [--image [--tag vX.Y.Z | --local]] [--dry-run]"
TRACK_MAIN=0
IMAGE_MODE=0
DRY_RUN=0
LOCAL_SRC=0
IMAGE_TAG=""
PANEL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --main) TRACK_MAIN=1 ;;
    --image) IMAGE_MODE=1 ;;
    --local) LOCAL_SRC=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --tag)
      [ $# -ge 2 ] || { echo "--tag needs a value (e.g. --tag v1.3.0)" >&2; exit 1; }
      IMAGE_TAG="$2"
      shift
      ;;
    --tag=*) IMAGE_TAG="${1#--tag=}" ;;
    --panel)
      [ $# -ge 2 ] || { echo "--panel needs a value (e.g. --panel waveshare_7in5_v2)" >&2; exit 1; }
      PANEL="$2"
      shift
      ;;
    --panel=*) PANEL="${1#--panel=}" ;;
    -h|--help)
      echo "$USAGE"
      echo "  --main       check out origin/main instead of the latest release tag"
      echo "  --panel KEY  write DISPLAY_PANEL=KEY to the env file; keys starting with inky_"
      echo "               install the inky extra instead of pi"
      echo "  --image      chroot/image mode (SD-card image build): no services started, no"
      echo "               raspi-config, no per-device secrets; default panel waveshare_7in5_v2"
      echo "  --tag TAG    with --image: install this release tag (default: the latest one)"
      echo "  --local      with --image: install the commit of the checkout this script is in"
      echo "               (pull-request builds, where no release tag exists yet)"
      echo "  --dry-run    print what would be done (mode, panel, pip extra) and exit"
      exit 0
      ;;
    *)
      echo "unknown argument: $1 ($USAGE)" >&2
      exit 1
      ;;
  esac
  shift
done

if [ "$IMAGE_MODE" -eq 0 ] && { [ -n "$IMAGE_TAG" ] || [ "$LOCAL_SRC" -eq 1 ]; }; then
  echo "--tag and --local only make sense with --image ($USAGE)" >&2
  exit 1
fi
if [ -n "$IMAGE_TAG" ] && { [ "$LOCAL_SRC" -eq 1 ] || [ "$TRACK_MAIN" -eq 1 ]; }; then
  echo "--tag cannot be combined with --local or --main ($USAGE)" >&2
  exit 1
fi
if [ "$IMAGE_MODE" -eq 1 ] && [ -z "$PANEL" ]; then
  PANEL=waveshare_7in5_v2
fi
case "$PANEL" in
  *[!a-z0-9_]*)
    echo "invalid --panel value: $PANEL (lower-case letters, digits and underscores only)" >&2
    exit 1
    ;;
esac
# The pip extra that carries the panel's driver: Inky Impression boards use Pimoroni's
# library (the `inky` extra), everything else the Waveshare one (the `pi` extra).
PIP_EXTRA=pi
case "$PANEL" in
  inky_*) PIP_EXTRA=inky ;;
esac

if [ "$DRY_RUN" -eq 1 ]; then
  echo "mode:      $([ "$IMAGE_MODE" -eq 1 ] && echo image || echo normal)"
  echo "panel:     ${PANEL:-<unchanged>}"
  echo "pip extra: $PIP_EXTRA"
  echo "source:    $(if [ -n "$IMAGE_TAG" ]; then echo "tag $IMAGE_TAG"; elif [ "$LOCAL_SRC" -eq 1 ]; then echo "local checkout"; elif [ "$TRACK_MAIN" -eq 1 ]; then echo "origin/main"; else echo "latest release tag"; fi)"
  exit 0
fi

REPO_URL="${REPO_URL:-https://github.com/lukislp/studylife-display.git}"
PREFIX=/opt/studylife-display
SRC="$PREFIX/src"
VENV="$PREFIX/venv"
ENV_FILE=/etc/studylife-display.env
STATE_DIR=/var/lib/studylife-display
# A stock Raspberry Pi OS image has no `pi` user any more (the person flashing it chooses the
# account in the Imager), so the image runs the services as a dedicated system account.
if [ "$IMAGE_MODE" -eq 1 ]; then
  SERVICE_USER="${SERVICE_USER:-studylife-display}"
elif [ -z "${SERVICE_USER:-}" ] && ! id -u pi >/dev/null 2>&1      && id -u studylife-display >/dev/null 2>&1; then
  # Re-running the installer on a system made from the SD-card image: its services run as
  # the image's service account (a system without `pi` could not have used the default).
  SERVICE_USER=studylife-display
else
  SERVICE_USER="${SERVICE_USER:-pi}"
fi
BOOT_DIR="${BOOT_DIR:-/boot/firmware}"

if [ "$(id -u)" -ne 0 ]; then
  echo "run with sudo: sudo bash $0" >&2
  exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> system packages (Pillow runtime, git, venv, lgpio build/runtime)"
if [ "$IMAGE_MODE" -eq 1 ]; then
  export DEBIAN_FRONTEND=noninteractive
fi
apt-get update
# swig and liblgpio-dev are needed to build the `lgpio` Python package's C extension (it has
# no prebuilt wheel for this platform); liblgpio-dev pulls in the liblgpio1 runtime library.
apt-get install -y python3-venv python3-pip git libopenjp2-7 fonts-dejavu-core swig liblgpio-dev \
  openssl

# Sets one dtparam/dtoverlay line in config.txt, idempotently: kept when already present,
# a commented-out stock line is switched on, otherwise it is appended. The lines passed in
# are plain [a-z0-9_=] text, so they can safely stand in as sed patterns.
config_txt_enable() {
  local file="$1" line="$2"
  if grep -qx "$line" "$file"; then
    return 0
  elif grep -qx "#[[:space:]]*$line" "$file"; then
    sed -i "s/^#[[:space:]]*$line\$/$line/" "$file"
  else
    printf '%s\n' "$line" >> "$file"
  fi
}

if [ "$IMAGE_MODE" -eq 1 ]; then
  echo "==> enabling SPI in $BOOT_DIR/config.txt (no raspi-config in a chroot)"
  [ -f "$BOOT_DIR/config.txt" ] || { echo "no $BOOT_DIR/config.txt - is the boot partition mounted?" >&2; exit 1; }
  config_txt_enable "$BOOT_DIR/config.txt" "dtparam=spi=on"
  case "$PANEL" in
    inky_*)
      # Pimoroni's documented requirements for the Inky Impression: I2C (the board's EEPROM
      # identifies the panel) and SPI with the chip-select handled by the library.
      config_txt_enable "$BOOT_DIR/config.txt" "dtparam=i2c_arm=on"
      config_txt_enable "$BOOT_DIR/config.txt" "dtoverlay=spi0-0cs"
      ;;
  esac
else
  echo "==> enabling SPI (the HAT is driven over SPI0)"
  raspi-config nonint do_spi 0
fi

if [ "$IMAGE_MODE" -eq 1 ]; then
  echo "==> service account $SERVICE_USER (system user, no login, SPI/GPIO access)"
  # The groups normally come with Raspberry Pi OS (udev rules grant them the device nodes);
  # creating them when absent is harmless and keeps the units' SupplementaryGroups= valid.
  for group in spi gpio; do
    getent group "$group" >/dev/null || groupadd --system "$group"
  done
  if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --user-group --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin --groups spi,gpio "$SERVICE_USER"
  fi
fi

TLS_CERT=/etc/studylife-display-tls.pem
TLS_KEY=/etc/studylife-display-tls.key
if [ "$IMAGE_MODE" -eq 1 ]; then
  # A private key in the image would be shared by every card flashed from it; the first-boot
  # unit generates one per device (and with the hostname the person chose in the Imager).
  echo "==> TLS certificate: left to the first boot (image mode)"
elif [ ! -f "$TLS_CERT" ] || [ ! -f "$TLS_KEY" ]; then
  echo "==> self-signed TLS certificate for DISPLAY_TLS=true ($TLS_CERT)"
  # Not for trust (it is self-signed; browsers show the interstitial once regardless) - only
  # for StudyLife's redirect-URI policy, which accepts https from anywhere but plain http
  # only from localhost. 10 years so nobody has to think about renewing it; the mDNS name
  # covers the URL everything else on this Pi already uses (the setup screen's QR code, the
  # suggested DISPLAY_PUBLIC_BASE_URL). IP SANs are not worth it - a DHCP lease can change.
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3650 \
    -subj "/CN=$(hostname).local" \
    -addext "subjectAltName=DNS:$(hostname).local,DNS:$(hostname)" \
    -keyout "$TLS_KEY" -out "$TLS_CERT"
  chown root:"$SERVICE_USER" "$TLS_KEY"
  chmod 0640 "$TLS_KEY"
  chmod 0644 "$TLS_CERT"
else
  echo "==> keeping existing TLS certificate ($TLS_CERT)"
fi

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
elif [ -n "$IMAGE_TAG" ]; then
  git -C "$SRC" rev-parse -q --verify "refs/tags/$IMAGE_TAG^{commit}" >/dev/null \
    || { echo "no such release tag: $IMAGE_TAG" >&2; exit 1; }
  echo "    release $IMAGE_TAG (--tag)"
  git -C "$SRC" checkout --force --detach --quiet "$IMAGE_TAG"
elif [ "$LOCAL_SRC" -eq 1 ]; then
  # Pull-request builds: the commit this script was started from, which no tag points at.
  LOCAL_REV="$(git -C "$HERE" rev-parse HEAD)"
  echo "    local checkout $LOCAL_REV (--local)"
  # A re-run finds the clone from the first one, which may predate this commit.
  git -C "$SRC" fetch --quiet "$HERE" "$LOCAL_REV"
  git -C "$SRC" checkout --force --detach --quiet "$LOCAL_REV"
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
if [ "$PIP_EXTRA" != "pi" ] && ! grep -Eq "^${PIP_EXTRA}[[:space:]]*=" "$SRC/pyproject.toml"; then
  echo "this release has no $PIP_EXTRA extra (needed by --panel $PANEL); use a newer release" >&2
  exit 1
fi
if [ "$IMAGE_MODE" -eq 1 ]; then
  # Nothing pip downloads should end up cached inside the image.
  export PIP_NO_CACHE_DIR=1
fi
TMPDIR="$PREFIX/tmp" "$VENV/bin/pip" install --upgrade "${SRC}[$PIP_EXTRA]"
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
if [ "$IMAGE_MODE" -eq 1 ]; then
  # The finished card always has the Bookworm/Trixie layout; a chroot cannot tell by mounts.
  PERSIST_PATH="$DEFAULT_PERSIST_PATH"
elif mountpoint -q /boot/firmware; then
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

if [ "$IMAGE_MODE" -eq 1 ]; then
  HAVE_TTY=0
elif [ -t 0 ]; then
  HAVE_TTY=1
else
  HAVE_TTY=0
fi

if [ ! -f "$ENV_FILE" ]; then
  echo "==> writing template $ENV_FILE (fill in the key!)"
  PLACEHOLDER_URL="https://studylife.example.com"
  STUDYLIFE_URL="$PLACEHOLDER_URL"
  if [ "$HAVE_TTY" -eq 1 ]; then
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
  SUGGESTED=""
  WEB_TOKEN=""
  if [ "$IMAGE_MODE" -eq 0 ]; then
    SUGGESTED="$(suggest_token)"
  fi
  if [ "$HAVE_TTY" -eq 1 ]; then
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
  elif [ "$IMAGE_MODE" -eq 0 ]; then
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
# Optional bearer-token JSON API under /api/ for other software (e.g. the studylife-hacs
# Home Assistant integration) - a separate token from the one above, off (every /api/
# route 404s) until this is at least 12 characters. See the README, JSON API section.
# DISPLAY_API_TOKEN=
# Serve the web interface over https, with the self-signed certificate this script just
# generated (or kept) at /etc/studylife-display-tls.pem. Needed for DISPLAY_PUBLIC_BASE_URL
# below to work without a separate reverse proxy or Tailscale.
# DISPLAY_TLS=false
# Optional https URL of this web interface (a Tailscale name, or this Pi's own address with
# DISPLAY_TLS=true above): StudyLife then redirects straight back to <url>/connect/callback
# when connecting the account, e.g. https://<hostname>.local:8795
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
# Automatically install a newer release once a day (studylife-display-update.timer); off by
# default, deploy/update.sh is a no-op when already current, so this is safe to turn on.
# DISPLAY_AUTO_UPDATE=false
EOF
    if [ "$PERSIST_PATH" != "$DEFAULT_PERSIST_PATH" ]; then
      printf 'DISPLAY_PERSIST_PATH=%s\n' "$PERSIST_PATH"
    fi
    if [ -n "$PANEL" ]; then
      printf 'DISPLAY_PANEL=%s
' "$PANEL"
    fi
    if [ "$IMAGE_MODE" -eq 1 ]; then
      # No token in the image: every card would share it. The first boot picks the one the
      # person put into the boot partition's setup.env, or generates one.
      echo "# DISPLAY_WEB_TOKEN is set by studylife-display-firstboot.service on the first boot."
    else
      printf 'DISPLAY_WEB_TOKEN=%s
' "$WEB_TOKEN"
    fi
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
  if [ -n "$PANEL" ]; then
    if grep -q '^DISPLAY_PANEL=' "$ENV_FILE"; then
      sed -i "s/^DISPLAY_PANEL=.*/DISPLAY_PANEL=$PANEL/" "$ENV_FILE"
    else
      printf 'DISPLAY_PANEL=%s\n' "$PANEL" >> "$ENV_FILE"
    fi
    echo "    DISPLAY_PANEL=$PANEL (--panel)"
  fi
  # The only line ever added to an existing env file: a non-default persist path, once.
  if [ "$PERSIST_PATH" != "$DEFAULT_PERSIST_PATH" ] \
     && ! grep -q '^DISPLAY_PERSIST_PATH=' "$ENV_FILE"; then
    echo "    adding DISPLAY_PERSIST_PATH=$PERSIST_PATH (this system's boot partition)"
    printf 'DISPLAY_PERSIST_PATH=%s\n' "$PERSIST_PATH" >> "$ENV_FILE"
  fi
fi

echo "==> mDNS advertisement (lets Home Assistant discover the display)"
# One DNS-SD service next to the <hostname>.local name avahi already answers; skipped with a
# log line when avahi is not installed, and never a reason to fail the install.
if [ "$IMAGE_MODE" -eq 1 ]; then
  # It carries the display's instance id, derived from /etc/machine-id: the first boot
  # writes it on the device itself, so no two cards advertise the same id.
  echo "    left to the first boot (image mode)"
else
  bash "$SRC/deploy/avahi-service.sh" || echo "    note: the mDNS advertisement could not be written"
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
install -m 0644 "$SRC/deploy/studylife-display-update.service" /etc/systemd/system/
install -m 0644 "$SRC/deploy/studylife-display-update.timer" /etc/systemd/system/
if [ "$IMAGE_MODE" -eq 1 ]; then
  # First-boot step (certificate, web token, setup.env import, mDNS file), see image/README.md.
  install -m 0644 "$SRC/image/firstboot/studylife-display-firstboot.service" /etc/systemd/system/
  # The commented template the person can fill in from a PC (instance URL, web token, panel).
  if [ ! -f "$BOOT_DIR/studylife-display/setup.env" ]; then
    mkdir -p "$BOOT_DIR/studylife-display"
    cp "$SRC/image/firstboot/setup.env.template" "$BOOT_DIR/studylife-display/setup.env"
  fi
  # The units name `User=pi`; the image has no such user. Drop-ins survive update.sh, which
  # only replaces the unit files themselves.
  for unit in studylife-display.service studylife-display-web.service; do
    install -d -m 0755 "/etc/systemd/system/$unit.d"
    printf '[Service]\nUser=%s\n' "$SERVICE_USER" > "/etc/systemd/system/$unit.d/10-service-user.conf"
  done
  # Enabled by symlink only: nothing is started inside a chroot, the units run on the first boot.
  systemctl enable studylife-display-firstboot.service studylife-display-restore.service \
    studylife-display-persist.path studylife-display-credentials.path studylife-display.timer \
    studylife-display-update.timer studylife-display-web.service
  echo
  echo "Image install done (panel $PANEL). Nothing was started; the units run on the first boot."
  exit 0
fi
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
systemctl enable --now studylife-display-update.timer
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
