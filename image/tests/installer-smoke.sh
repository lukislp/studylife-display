#!/usr/bin/env bash
# Exercises `deploy/install.sh --image` and the first-boot script for real, in a throwaway
# Debian Trixie container (no QEMU, no image): the same steps the image build runs in its
# chroot, minus the arm64 emulation. Run inside the container as root:
#
#   docker run --rm -v "$PWD:/repo:ro" debian:trixie bash /repo/image/tests/installer-smoke.sh
#
# /repo must contain a git checkout (the installer clones it); set APT_FORCE_HTTPS=1 on
# networks that block plain-http apt mirrors. Needs an arm64 container (--platform
# linux/arm64: native on an arm runner, QEMU-emulated elsewhere).
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

REPO="${REPO:-/repo}"
WORK=/tmp/studylife-src
PASS=0
check() { # check "description" command...
  local what="$1"; shift
  if "$@" >/dev/null 2>&1; then
    PASS=$((PASS + 1)); echo "ok   - $what"
  else
    echo "FAIL - $what"; exit 1
  fi
}
check_not() { # check_not "description" command...
  local what="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "FAIL - $what"; exit 1
  else
    PASS=$((PASS + 1)); echo "ok   - $what"
  fi
}
contains() { grep -q -- "$2" <<<"$1"; }

# ---------------------------------------------------------------- bootstrap
if [ "${APT_FORCE_HTTPS:-0}" = "1" ]; then
  # ca-certificates is needed for https, and comes over https: bootstrap it unverified once
  # (a throwaway container; the real packages are verified by apt's signatures anyway).
  sed -i 's#http://#https://#g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true
  apt-get -o Acquire::https::Verify-Peer=false -o Acquire::https::Verify-Host=false update -qq
  apt-get -o Acquire::https::Verify-Peer=false -o Acquire::https::Verify-Host=false \
    install -y -qq ca-certificates >/dev/null
fi
# liblgpio-dev (and python3-lgpio) come from the Raspberry Pi archive, not from Debian, so the
# container gets that repository the way Raspberry Pi OS has it. arm64 only, like the image.
apt-get update -qq
apt-get install -y -qq curl gpg gpgv >/dev/null
# Debian's sqv rejects the archive key's SHA-1 self-signature (Raspberry Pi OS ships its own
# apt policy for that); this throwaway container verifies with gpgv instead.
echo 'APT::Key::GPGVCommand "/usr/bin/gpgv";' > /etc/apt/apt.conf.d/99gpgv
curl -fsSL https://archive.raspberrypi.com/debian/raspberrypi.gpg.key | gpg --dearmor   > /usr/share/keyrings/raspberrypi-archive.gpg
echo "deb [signed-by=/usr/share/keyrings/raspberrypi-archive.gpg] https://archive.raspberrypi.com/debian/ trixie main"   > /etc/apt/sources.list.d/raspi.list
apt-get update -qq
# What stock Raspberry Pi OS Lite already has and the installer relies on.
apt-get install -y -qq systemd git gcc python3-dev openssl >/dev/null
git config --global --add safe.directory '*'  # the mounted checkout belongs to another uid
git clone -q --no-hardlinks "$REPO" "$WORK"

# ---------------------------------------------------------------- argument handling
for key in waveshare_7in5_v2 waveshare_7in5b_v2 waveshare_7in5_v1 waveshare_7in5_hd waveshare_7in3f; do
  out="$(bash "$WORK/deploy/install.sh" --image --panel "$key" --dry-run)"
  check "dry run $key -> pi extra" contains "$out" "pip extra: pi"
done
out="$(bash "$WORK/deploy/install.sh" --image --panel inky_impression_7in3 --dry-run)"
check "dry run inky_impression_7in3 -> inky extra" contains "$out" "pip extra: inky"
out="$(bash "$WORK/deploy/install.sh" --image --dry-run)"
check "image mode defaults to waveshare_7in5_v2" contains "$out" "panel:     waveshare_7in5_v2"
out="$(bash "$WORK/deploy/install.sh" --dry-run)"
check "normal mode leaves the panel alone" contains "$out" "panel:     <unchanged>"
check_not "an invalid panel key is refused" bash "$WORK/deploy/install.sh" --panel 'x;rm' --dry-run
check_not "--tag without --image is refused" bash "$WORK/deploy/install.sh" --tag v1.0.0 --dry-run

# ---------------------------------------------------------------- the image-mode install
mkdir -p /boot/firmware
cat > /boot/firmware/config.txt <<'CFG'
# For more options see the Raspberry Pi documentation
dtparam=audio=on
#dtparam=spi=on
[all]
CFG
: > /etc/machine-id
bash "$WORK/deploy/install.sh" --image --local

check "SPI switched on exactly once" test "$(grep -c '^dtparam=spi=on$' /boot/firmware/config.txt)" = 1
check "the commented stock line is gone" test "$(grep -c '^#dtparam=spi=on' /boot/firmware/config.txt)" = 0
bash "$WORK/deploy/install.sh" --image --local >/dev/null
check "re-running keeps SPI on exactly once" test "$(grep -c 'dtparam=spi=on' /boot/firmware/config.txt)" = 1
check "env file has the default panel" grep -qx 'DISPLAY_PANEL=waveshare_7in5_v2' /etc/studylife-display.env
check "env file has no web token" test "$(grep -c '^DISPLAY_WEB_TOKEN=' /etc/studylife-display.env)" = 0
check "service account exists" id studylife-display
check "state directory is empty" test -z "$(ls -A /var/lib/studylife-display)"
check "no TLS key in the image" test ! -e /etc/studylife-display-tls.key
check "no mDNS file in the image" test ! -e /etc/avahi/services/studylife-display.service
check "setup.env template on the boot partition" test -f /boot/firmware/studylife-display/setup.env
for unit in studylife-display-firstboot.service studylife-display-restore.service \
    studylife-display-persist.path studylife-display-credentials.path studylife-display.timer \
    studylife-display-update.timer studylife-display-web.service; do
  check "$unit is enabled" test "$(systemctl is-enabled "$unit")" = enabled
done
check "service user drop-in for the refresh unit" grep -q '^User=studylife-display$' \
  /etc/systemd/system/studylife-display.service.d/10-service-user.conf
check "service user drop-in for the web unit" grep -q '^User=studylife-display$' \
  /etc/systemd/system/studylife-display-web.service.d/10-service-user.conf

VENV=/opt/studylife-display/venv
check "package imports" "$VENV/bin/python" -c "import studylife_display"
mkdir -p /tmp/preview
check "hardware-free sample frame renders" env DISPLAY_DRIVER=file DISPLAY_STATE_PATH=/tmp/preview/last.json \
  "$VENV/bin/studylife-display" preview --sample --out /tmp/preview/sample.png
check "setup screen renders (no key)" env DISPLAY_DRIVER=file DISPLAY_STATE_PATH=/tmp/preview/last.json STUDYLIFE_BASE_URL=https://studylife.example.com \
  STUDYLIFE_API_KEY= DISPLAY_SETUP_URL=http://pi.local:8795/connect \
  "$VENV/bin/studylife-display" preview --out /tmp/preview/setup.png
check "preview files are PNGs" test "$(head -c 4 /tmp/preview/setup.png | tail -c 3)" = PNG

# ---------------------------------------------------------------- the scanner
SCAN="$WORK/image/scan-rootfs.sh"
rm -f /var/lib/dbus/machine-id  # the container has one; build-image.sh removes it in its clean step
check "scanner passes on the freshly installed image state" bash "$SCAN" / /boot/firmware

# ---------------------------------------------------------------- first boot
AVAHI_DIR=/etc/avahi/services
mkdir -p "$AVAHI_DIR"
FB="$WORK/image/firstboot/firstboot.sh"
echo 11111111111111111111111111111111 > /etc/machine-id
bash "$FB"
check "first boot issued the certificate" test -s /etc/studylife-display-tls.pem
check "certificate key is mode 640" test "$(stat -c %a /etc/studylife-display-tls.key)" = 640
check "first boot generated a web token" grep -Eq '^DISPLAY_WEB_TOKEN=[0-9a-f]{32}$' /etc/studylife-display.env
check "token also readable on the boot partition" test -s /boot/firmware/studylife-display/web-token.txt
token_value="$(grep -h '^DISPLAY_WEB_TOKEN=' /etc/studylife-display.env | cut -d= -f2)"
check_not "the token is not printed by a later run" bash -c "bash '$FB' 2>&1 | grep -q '$token_value'"
id1="$(grep -o 'id=[0-9a-f]*' "$AVAHI_DIR/studylife-display.service" | head -1)"
check "mDNS file carries an id" test -n "$id1"
check_not "scanner now fails (certificate, token, mDNS file present)" bash "$SCAN" / /boot/firmware
cert_sum="$(sha256sum /etc/studylife-display-tls.pem)"
token_line="$(grep '^DISPLAY_WEB_TOKEN=' /etc/studylife-display.env)"
bash "$FB"
check "second run keeps the certificate" test "$cert_sum" = "$(sha256sum /etc/studylife-display-tls.pem)"
check "second run keeps the token" test "$token_line" = "$(grep '^DISPLAY_WEB_TOKEN=' /etc/studylife-display.env)"

# A different machine id (another card) yields a different advertised id.
echo 22222222222222222222222222222222 > /etc/machine-id
bash "$FB"
id2="$(grep -o 'id=[0-9a-f]*' "$AVAHI_DIR/studylife-display.service" | head -1)"
check "another machine id advertises another instance id" test "$id1" != "$id2"

# setup.env import, written the way a Windows editor would (CRLF).
printf 'STUDYLIFE_BASE_URL=https://study.example.org\r\nDISPLAY_WEB_TOKEN=my-own-token-123\r\nDISPLAY_PANEL=waveshare_7in5_hd\r\nDISPLAY_ROTATE=90\r\nFOO=bar\r\nDISPLAY_LANGUAGE="en"\r\n' \
  > /boot/firmware/studylife-display/setup.env
out="$(bash "$FB")"
check "setup.env: URL applied" grep -qx 'STUDYLIFE_BASE_URL=https://study.example.org' /etc/studylife-display.env
check "setup.env: token applied" grep -qx 'DISPLAY_WEB_TOKEN=my-own-token-123' /etc/studylife-display.env
check "setup.env: panel applied" grep -qx 'DISPLAY_PANEL=waveshare_7in5_hd' /etc/studylife-display.env
check "setup.env: quoted value applied" grep -qx 'DISPLAY_LANGUAGE=en' /etc/studylife-display.env
check "setup.env: invalid rotation refused" test "$(grep -c '^DISPLAY_ROTATE=90' /etc/studylife-display.env)" = 0
check "setup.env: unknown key reported" contains "$out" "ignoring unknown key FOO"
check "setup.env: applied lines no longer carry values" test "$(grep -c 'my-own-token-123' /boot/firmware/studylife-display/setup.env)" = 0
check "setup.env: a chosen token removes the generated one's file" test ! -e /boot/firmware/studylife-display/web-token.txt
check "env file keeps its owner and mode" test "$(stat -c '%U:%G %a' /etc/studylife-display.env)" = "root:studylife-display 640"

# ---------------------------------------------------------------- inky guard
if bash "$WORK/deploy/install.sh" --image --local --panel inky_impression_7in3 >/tmp/inky.log 2>&1; then
  check "inky install wrote the panel key" grep -qx 'DISPLAY_PANEL=inky_impression_7in3' /etc/studylife-display.env
  check "inky install enabled I2C" grep -qx 'dtparam=i2c_arm=on' /boot/firmware/config.txt
else
  check "without an inky extra the installer says so and stops" grep -q 'has no inky extra' /tmp/inky.log
fi

echo "$PASS checks passed"
