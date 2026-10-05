#!/usr/bin/env bash
# Builds the flashable SD-card image: the pinned stock Raspberry Pi OS Lite image, customised
# offline in a chroot by deploy/install.sh --image, cleaned, scanned, and xz-compressed.
#
#   sudo image/build-image.sh --version 1.4.0 --tag v1.4.0 --out dist       # release build
#   sudo image/build-image.sh --version 0.0.0-pr --local --out dist          # PR build
#
# Needs root, a Debian/Ubuntu host with: curl xz-utils util-linux e2fsprogs fdisk git systemd
# (and qemu-user-static + binfmt-support when the host is not arm64).
# No third-party actions, no container images: plain tools. Produces in --out:
#   studylife-display-<version>.img.xz          the image
#   studylife-display-<version>.img.xz.sha256   its checksum
#   studylife-display-<version>.pip-freeze.txt  Python packages inside the image
#   studylife-display-<version>.packages.txt    apt packages inside the image
#   image-meta.env                              sizes and hashes (input of make-os-list.sh)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
# shellcheck source=image/base-image.env
. "$HERE/base-image.env"

VERSION=""
TAG=""
LOCAL=0
PANEL=waveshare_7in5_v2
OUT="$REPO_ROOT/dist"
WORK="${WORK_DIR:-/tmp/studylife-image-work}"
# Extra room while building: the Waveshare driver is installed from a git checkout of a large
# vendor repository. The image is shrunk again at the end; the stock image expands its root
# partition to the card on first boot anyway (the `resize` word in cmdline.txt).
GROW_MIB="${GROW_MIB:-3072}"
# Free space left in the root filesystem after the shrink.
HEADROOM_MIB="${HEADROOM_MIB:-256}"

usage() {
  echo "usage: sudo $0 --version X.Y.Z (--tag vX.Y.Z | --local) [--panel KEY] [--out DIR]" >&2
  exit 1
}
while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="${2:?}"; shift ;;
    --tag) TAG="${2:?}"; shift ;;
    --local) LOCAL=1 ;;
    --panel) PANEL="${2:?}"; shift ;;
    --out) OUT="${2:?}"; shift ;;
    *) usage ;;
  esac
  shift
done
[ -n "$VERSION" ] || usage
{ [ -n "$TAG" ] || [ "$LOCAL" -eq 1 ]; } || usage
[ -z "$TAG" ] || [ "$LOCAL" -eq 0 ] || usage
[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }

NAME="studylife-display-$VERSION"
IMG="$WORK/$NAME.img"
MNT="$WORK/mnt"
LOOP=""
CHROOT_MOUNTED=0

log() { echo "==> $*"; }

cleanup() {
  local rc=$?
  set +e
  if [ "$CHROOT_MOUNTED" -eq 1 ]; then
    umount "$MNT/dev/pts" "$MNT/dev" "$MNT/proc" "$MNT/sys" 2>/dev/null
  fi
  mountpoint -q "$MNT/boot/firmware" && umount "$MNT/boot/firmware"
  mountpoint -q "$MNT" && umount "$MNT"
  [ -z "$LOOP" ] || losetup -d "$LOOP"
  exit "$rc"
}
trap cleanup EXIT

rm -rf "$WORK"
mkdir -p "$WORK" "$MNT" "$OUT"

# ------------------------------------------------------------------ 1. base image
log "downloading the pinned base image ($BASE_IMAGE_DATE)"
curl --fail --silent --show-error --location --retry 3 --retry-delay 5 \
  --output "$WORK/base.img.xz" "$BASE_IMAGE_URL"
echo "$BASE_IMAGE_SHA256  $WORK/base.img.xz" | sha256sum --check --strict -
log "decompressing"
xz --decompress --stdout "$WORK/base.img.xz" > "$IMG"
rm -f "$WORK/base.img.xz"

# ------------------------------------------------------------------ 2. grow + mount
log "growing the root partition by $GROW_MIB MiB"
truncate --size="+${GROW_MIB}M" "$IMG"
# Partition 2 takes everything that is now free at the end of the image.
echo ", +" | sfdisk --no-reread --no-tell-kernel -N 2 "$IMG" >/dev/null
LOOP="$(losetup --find --show --partscan "$IMG")"
ROOT_DEV="${LOOP}p2"
BOOT_DEV="${LOOP}p1"
e2fsck -fp "$ROOT_DEV"
resize2fs "$ROOT_DEV"
mount "$ROOT_DEV" "$MNT"
mount "$BOOT_DEV" "$MNT/boot/firmware"
echo "--- /boot/firmware/cmdline.txt"; cat "$MNT/boot/firmware/cmdline.txt"
echo "--- /etc/fstab"; cat "$MNT/etc/fstab"
echo "--- /boot/firmware/issue.txt"; cat "$MNT/boot/firmware/issue.txt" 2>/dev/null || true
echo "--- machine-id / dbus machine-id / ssh host keys in the stock image"
ls -l "$MNT/etc/machine-id" "$MNT/var/lib/dbus/machine-id" 2>&1 || true
ls "$MNT"/etc/ssh/ssh_host_* 2>&1 || true
df -h "$MNT"

# ------------------------------------------------------------------ 3. chroot
if [ "$(uname -m)" != "aarch64" ]; then
  log "host is $(uname -m): using qemu-user-static"
  command -v qemu-aarch64-static >/dev/null || { echo "qemu-user-static is not installed" >&2; exit 1; }
  install -m 0755 "$(command -v qemu-aarch64-static)" "$MNT/usr/bin/qemu-aarch64-static"
fi
# Plain (non-recursive) mounts on purpose: a recursive bind of /dev and /sys leaves the root
# filesystem's block device "in use" after everything is unmounted again (e2fsck refuses to
# run), and /sys/fs/cgroup refuses to unmount.
mount --bind /dev "$MNT/dev"
mount --bind /dev/pts "$MNT/dev/pts"
mount -t proc proc "$MNT/proc"
mount -t sysfs sysfs "$MNT/sys"
CHROOT_MOUNTED=1
# DNS inside the chroot: the stock resolv.conf is a symlink into /run, which is empty here.
mv "$MNT/etc/resolv.conf" "$MNT/etc/resolv.conf.image-orig"
cp -L /etc/resolv.conf "$MNT/etc/resolv.conf"
# No daemon may be started by a package's postinst inside the chroot.
printf '#!/bin/sh\nexit 101\n' > "$MNT/usr/sbin/policy-rc.d"
chmod 0755 "$MNT/usr/sbin/policy-rc.d"

in_chroot() {
  chroot "$MNT" /usr/bin/env -i HOME=/root LANG=C.UTF-8 TERM=dumb \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    DEBIAN_FRONTEND=noninteractive "$@"
}

log "copying the repository into the chroot"
git -c safe.directory='*' clone --quiet --no-hardlinks "$REPO_ROOT" "$MNT/tmp/studylife-src"

log "running deploy/install.sh --image (panel $PANEL)"
if [ -n "$TAG" ]; then
  in_chroot bash /tmp/studylife-src/deploy/install.sh --image --panel "$PANEL" --tag "$TAG"
else
  in_chroot bash /tmp/studylife-src/deploy/install.sh --image --panel "$PANEL" --local
fi

# ------------------------------------------------------------------ 4. verify inside
log "verifying the installation"
VENV=/opt/studylife-display/venv
INSTALLED="$(in_chroot "$VENV/bin/studylife-display" --version)"
echo "installed: $INSTALLED"
if [ -n "$TAG" ] && ! grep -q "${TAG#v}" <<<"$INSTALLED"; then
  echo "the installed version ($INSTALLED) does not match the tag $TAG" >&2
  exit 1
fi
in_chroot "$VENV/bin/python" -c "import studylife_display; print('import ok', studylife_display.package_version())"
in_chroot mkdir -p /tmp/preview
# Hardware-free smoke: render the sample dashboard and the setup screen through the file driver.
in_chroot env DISPLAY_DRIVER=file DISPLAY_STATE_PATH=/tmp/preview/last.json "$VENV/bin/studylife-display" preview --sample --out /tmp/preview/sample.png
in_chroot env DISPLAY_DRIVER=file DISPLAY_STATE_PATH=/tmp/preview/last.json STUDYLIFE_BASE_URL=https://studylife.example.com STUDYLIFE_API_KEY= \
  DISPLAY_SETUP_URL=http://studylife-display.local:8795/connect \
  "$VENV/bin/studylife-display" preview --out /tmp/preview/setup.png
[ -s "$MNT/tmp/preview/sample.png" ] && [ -s "$MNT/tmp/preview/setup.png" ]
for unit in studylife-display-firstboot.service studylife-display-restore.service \
    studylife-display-persist.path studylife-display-credentials.path studylife-display.timer \
    studylife-display-update.timer studylife-display-web.service; do
  state="$(systemctl --root="$MNT" is-enabled "$unit")"
  echo "$unit: $state"
  [ "$state" = "enabled" ] || { echo "$unit is not enabled" >&2; exit 1; }
done
grep -qx 'dtparam=spi=on' "$MNT/boot/firmware/config.txt"
echo "--- DISPLAY_PANEL in the env file"; grep '^DISPLAY_PANEL=' "$MNT/etc/studylife-display.env"
# The image never needs reflashing for updates: the Pi's update machinery keeps working.
in_chroot test -x /opt/studylife-display/src/deploy/update.sh
in_chroot git -C /opt/studylife-display/src remote get-url origin

# Inventory for the release (the poor man's SBOM): exact Python and apt package versions.
in_chroot "$VENV/bin/pip" freeze > "$OUT/$NAME.pip-freeze.txt"
in_chroot dpkg-query -W -f='${binary:Package}\t${Version}\n' > "$OUT/$NAME.packages.txt"

# ------------------------------------------------------------------ 5. clean
log "cleaning"
in_chroot apt-get clean
rm -rf "$MNT"/var/lib/apt/lists/* "$MNT"/var/cache/apt/*.bin "$MNT"/tmp/* "$MNT"/var/tmp/* \
  "$MNT"/root/.cache "$MNT"/root/.bash_history "$MNT"/opt/studylife-display/tmp \
  "$MNT"/var/lib/systemd/random-seed "$MNT"/root/.gitconfig "$MNT"/var/lib/studylife-display/* 2>/dev/null || true
for home in "$MNT"/home/*; do
  [ -d "$home" ] && rm -rf "$home/.cache" "$home/.bash_history"
done
# Logs and the journal of the build, not of any device.
find "$MNT/var/log" -type f -delete
rm -rf "$MNT/var/log/journal"
# Per-device identity: the stock "uninitialized" machine-id (systemd makes a real one on the
# first boot) and no SSH host keys (cloud-init / the Pi's ssh setup generates them per device).
printf 'uninitialized
' > "$MNT/etc/machine-id"
chmod 0444 "$MNT/etc/machine-id"
if [ -e "$MNT/var/lib/dbus/machine-id" ] && [ ! -L "$MNT/var/lib/dbus/machine-id" ]; then
  rm -f "$MNT/var/lib/dbus/machine-id"
  ln -s /etc/machine-id "$MNT/var/lib/dbus/machine-id"
fi
rm -f "$MNT"/etc/ssh/ssh_host_*
# Undo the chroot plumbing.
rm -f "$MNT/usr/sbin/policy-rc.d" "$MNT/usr/bin/qemu-aarch64-static" "$MNT/etc/resolv.conf"
mv "$MNT/etc/resolv.conf.image-orig" "$MNT/etc/resolv.conf"
umount "$MNT/dev/pts" "$MNT/dev" "$MNT/proc" "$MNT/sys"
CHROOT_MOUNTED=0

# ------------------------------------------------------------------ 6. scan
log "scanning the image for forbidden artifacts"
bash "$HERE/scan-rootfs.sh" "$MNT" "$MNT/boot/firmware"

# ------------------------------------------------------------------ 7. seal
log "shrinking the root filesystem (the stock image expands to the card on first boot)"
umount "$MNT/boot/firmware"
umount "$MNT"
# udev may still be probing the freshly unmounted partitions, which makes e2fsck fail with
# "is in use" for a moment: let it settle, and retry.
sync
udevadm settle --timeout=60 || true
fsck_root() { # fsck_root -fp|-fn
  local attempt
  for attempt in 1 2 3 4 5 6; do
    if e2fsck "$1" "$ROOT_DEV"; then
      return 0
    else
      rc=$?
    fi
    # 1 = errors corrected, which -p reports for harmless things like a replayed journal.
    [ "$rc" -le 1 ] && return 0
    echo "e2fsck exited $rc (attempt $attempt), retrying" >&2
    grep -s "${ROOT_DEV##*/}" /proc/self/mountinfo >&2 || true
    (fuser -v "$ROOT_DEV" || true) >&2 2>&1
    sleep 5
  done
  return 1
}
fsck_root -fp
resize2fs -M "$ROOT_DEV"
BLOCK_COUNT="$(dumpe2fs -h "$ROOT_DEV" 2>/dev/null | awk -F: '/^Block count/ { gsub(/ /, "", $2); print $2 }')"
BLOCK_SIZE="$(dumpe2fs -h "$ROOT_DEV" 2>/dev/null | awk -F: '/^Block size/ { gsub(/ /, "", $2); print $2 }')"
NEW_BYTES=$((BLOCK_COUNT * BLOCK_SIZE + HEADROOM_MIB * 1024 * 1024))
resize2fs "$ROOT_DEV" "$((NEW_BYTES / 1024))K"
# Give back the space that was never used: trim what the compaction left behind, so the
# image is mostly zeros there and compresses well.
mount "$ROOT_DEV" "$MNT"
fstrim --verbose "$MNT" || true
umount "$MNT"
sync
udevadm settle --timeout=60 || true
fsck_root -fn
losetup -d "$LOOP"
LOOP=""
LABEL="$(sfdisk -d "$IMG" | awk '/^label:/ { print $2 }')"
if [ "$LABEL" = "dos" ]; then
  START="$(sfdisk -d "$IMG" | sed -n '/2 : start=/ s/.*start= *\([0-9]*\),.*/\1/p')"
  NEW_SECTORS=$((NEW_BYTES / 512))
  echo "$START,$NEW_SECTORS" | sfdisk --no-reread --no-tell-kernel -N 2 "$IMG" >/dev/null
  truncate --size=$(((START + NEW_SECTORS) * 512)) "$IMG"
  sfdisk -d "$IMG"
else
  echo "partition table is '$LABEL', not dos: leaving the image at its grown size"
fi
# One more check on the final file.
LOOP="$(losetup --find --show --partscan "$IMG")"
ROOT_DEV="${LOOP}p2"
udevadm settle --timeout=60 || true
fsck_root -fn
losetup -d "$LOOP"
LOOP=""

log "checksumming and compressing"
RAW_SIZE="$(stat -c %s "$IMG")"
RAW_SHA256="$(sha256sum "$IMG" | cut -d' ' -f1)"
xz --threads=0 -6 --stdout "$IMG" > "$OUT/$NAME.img.xz"
rm -f "$IMG"
( cd "$OUT" && sha256sum "$NAME.img.xz" > "$NAME.img.xz.sha256" )
XZ_SIZE="$(stat -c %s "$OUT/$NAME.img.xz")"
cat > "$OUT/image-meta.env" <<META
IMAGE_NAME=$NAME
IMAGE_VERSION=$VERSION
EXTRACT_SIZE=$RAW_SIZE
EXTRACT_SHA256=$RAW_SHA256
DOWNLOAD_SIZE=$XZ_SIZE
BASE_IMAGE_DATE=$BASE_IMAGE_DATE
PANEL=$PANEL
META
log "done: $OUT/$NAME.img.xz ($XZ_SIZE bytes compressed, $RAW_SIZE raw)"
