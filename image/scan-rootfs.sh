#!/usr/bin/env bash
# Fails when the finished image contains something that must be created per device or that
# should never ship: private keys, a machine id, credentials, a web token, cached data.
#
#   scan-rootfs.sh ROOT [BOOT]     ROOT = the mounted root filesystem, BOOT = the boot partition
#
# Prints every finding and exits 1 if there is at least one. Run by the image build after the
# chroot step and by image/tests/installer-smoke.sh (which also proves that it does fail).
set -uo pipefail

ROOT="${1:?usage: scan-rootfs.sh ROOT [BOOT]}"
BOOT="${2:-}"
FAILED=0

fail() {
  echo "FORBIDDEN: $*"
  FAILED=1
}

# --- per-device identity -------------------------------------------------------------
for f in "$ROOT"/etc/ssh/ssh_host_*; do
  [ -e "$f" ] && fail "SSH host key ${f#"$ROOT"} (must be generated on the device)"
done

mid="$ROOT/etc/machine-id"
if [ -e "$mid" ]; then
  content="$(tr -d '[:space:]' < "$mid")"
  case "$content" in
    ''|uninitialized) ;;
    *) fail "/etc/machine-id holds an id (every card would share it)" ;;
  esac
fi
dbus_mid="$ROOT/var/lib/dbus/machine-id"
if [ -e "$dbus_mid" ] && [ ! -L "$dbus_mid" ] && [ -s "$dbus_mid" ]; then
  fail "/var/lib/dbus/machine-id is a non-empty regular file"
fi
[ -e "$ROOT/var/lib/systemd/random-seed" ] && fail "/var/lib/systemd/random-seed (same seed on every card)"

# --- StudyLife secrets and state -----------------------------------------------------
for f in etc/studylife-display-tls.pem etc/studylife-display-tls.key \
         etc/avahi/services/studylife-display.service; do
  [ -e "$ROOT/$f" ] && fail "/$f (per-device, created on first boot)"
done
state="$ROOT/var/lib/studylife-display"
if [ -d "$state" ]; then
  while IFS= read -r f; do
    fail "state file ${f#"$ROOT"} (the state directory must ship empty)"
  done < <(find "$state" -mindepth 1 -type f)
fi
env_file="$ROOT/etc/studylife-display.env"
if [ -f "$env_file" ]; then
  for key in STUDYLIFE_API_KEY DISPLAY_WEB_TOKEN DISPLAY_API_TOKEN DISPLAY_PUBLIC_BASE_URL; do
    grep -Eq "^$key=.+" "$env_file" && fail "$key has a value in /etc/studylife-display.env"
  done
else
  fail "/etc/studylife-display.env is missing (the installer did not run?)"
fi
if [ -n "$BOOT" ] && [ -d "$BOOT/studylife-display" ]; then
  for f in settings.json web-token.txt credentials.pending.json; do
    [ -e "$BOOT/studylife-display/$f" ] && fail "boot partition studylife-display/$f"
  done
  if [ -f "$BOOT/studylife-display/setup.env" ] \
     && grep -Eq '^[[:space:]]*(STUDYLIFE_BASE_URL|DISPLAY_WEB_TOKEN|DISPLAY_PANEL|DISPLAY_ROTATE|DISPLAY_LANGUAGE|STUDYLIFE_TIMEZONE)=' "$BOOT/studylife-display/setup.env"; then
    fail "boot partition studylife-display/setup.env has an uncommented value"
  fi
fi

# --- anything a person left behind ---------------------------------------------------
for f in "$ROOT"/root/.bash_history "$ROOT"/home/*/.bash_history "$ROOT"/root/.ssh/authorized_keys \
         "$ROOT"/home/*/.ssh/authorized_keys "$ROOT"/etc/wpa_supplicant/wpa_supplicant.conf; do
  [ -e "$f" ] && fail "${f#"$ROOT"}"
done
for f in "$ROOT"/etc/NetworkManager/system-connections/*; do
  [ -e "$f" ] && fail "network connection ${f#"$ROOT"} (may hold a Wi-Fi password)"
done
[ -d "$ROOT/var/lib/cloud/instances" ] && [ -n "$(ls -A "$ROOT/var/lib/cloud/instances" 2>/dev/null)" ] \
  && fail "cloud-init instance data in /var/lib/cloud/instances (the image was booted?)"

# PEM private keys outside installed package trees (libraries ship test keys; the places
# below are where a key of ours or of the build host would end up).
while IFS= read -r f; do
  fail "private key material in ${f#"$ROOT"}"
done < <(grep -rIl \
           -e '-----BEGIN [A-Z ]*PRIVATE KEY-----' \
           "$ROOT/etc" "$ROOT/root" "$ROOT/home" "$ROOT/var/lib" "$ROOT/opt/studylife-display/src" \
           ${BOOT:+"$BOOT"} 2>/dev/null)

if [ "$FAILED" -eq 0 ]; then
  echo "scan: no forbidden artifacts in $ROOT"
fi
exit "$FAILED"
