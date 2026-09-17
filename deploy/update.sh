#!/usr/bin/env bash
# Updates studylife-display on the Pi to the latest GitHub release, or to a given tag.
#
#   sudo bash /opt/studylife-display/src/deploy/update.sh              # latest release
#   sudo bash /opt/studylife-display/src/deploy/update.sh --tag v1.3.0 # that release
#   sudo bash /opt/studylife-display/src/deploy/update.sh --check      # current vs latest
#
# --check prints both versions and exits 0 when the install is current, 1 when a newer
# release exists (2 when GitHub cannot be reached), without changing anything. An update
# checks the tag out under /opt/studylife-display/src, reinstalls the package into the
# virtualenv, re-installs the unit files from deploy/ (so new units land), reloads systemd,
# restarts the web service and runs one refresh. Re-running on the tag that is already
# installed does nothing but say so; --force does the whole thing anyway.
#
# The overlay filesystem (README, "SD-card protection") makes /opt and /etc RAM-backed, so an
# update done with it on would vanish at the next reboot. The script refuses in that state
# (--force overrides it for a test run) and prints the three steps to do it properly.
set -euo pipefail

PREFIX=/opt/studylife-display
SRC="$PREFIX/src"
VENV="$PREFIX/venv"
REPO="lukislp/studylife-display"
API_URL="https://api.github.com/repos/$REPO/releases/latest"

usage() {
  cat <<EOF
usage: sudo bash $0 [--check] [--tag vX.Y.Z] [--force]
  --check       print the installed and the latest release, exit 1 when behind, change nothing
  --tag vX.Y.Z  install that release instead of the latest one
  --force       proceed although the overlay filesystem is on, or the tag is already installed
EOF
}

die() {
  echo "error: $*" >&2
  exit "${2:-1}"
}

latest_tag() {
  # The releases endpoint is public; no token, and 60 requests per hour per IP are plenty.
  local json
  json="$(curl -fsSL --max-time 15 -H 'Accept: application/vnd.github+json' "$API_URL")" \
    || return 1
  printf '%s' "$json" | python3 -c 'import json, sys; print(json.load(sys.stdin)["tag_name"])'
}

current_tag() {
  # The exact tag the checkout sits on, or the short commit when it is not on a tag (a
  # developer install that tracks main).
  git -C "$SRC" describe --tags --exact-match 2>/dev/null \
    || git -C "$SRC" rev-parse --short HEAD
}

overlay_active() {
  # Raspberry Pi OS with `raspi-config nonint enable_overlayfs`: raspi-config answers 0
  # for "on", and the root mount shows up as an overlay either way.
  if command -v raspi-config >/dev/null 2>&1 \
     && [ "$(raspi-config nonint get_overlay_now 2>/dev/null || true)" = "0" ]; then
    return 0
  fi
  awk '$2 == "/" && $3 == "overlay" { found = 1 } END { exit !found }' /proc/mounts
}

main() {
  local check=0 force=0 tag="" current latest target
  while [ $# -gt 0 ]; do
    case "$1" in
      --check) check=1 ;;
      --force) force=1 ;;
      --tag)
        [ $# -ge 2 ] || die "--tag needs a value (e.g. --tag v1.3.0)"
        tag="$2"
        shift
        ;;
      --tag=*) tag="${1#--tag=}" ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        usage >&2
        die "unknown argument: $1"
        ;;
    esac
    shift
  done

  if [ "$(id -u)" -ne 0 ]; then
    die "run with sudo: sudo bash $0 $*"
  fi
  [ -d "$SRC/.git" ] || die "no checkout at $SRC - run deploy/install.sh first"
  [ -x "$VENV/bin/pip" ] || die "no virtualenv at $VENV - run deploy/install.sh first"

  current="$(current_tag)"
  if [ "$check" -eq 1 ]; then
    latest="$(latest_tag)" || die "could not fetch the latest release from GitHub" 2
    echo "installed: $current"
    echo "latest:    $latest"
    if [ "$current" = "$latest" ]; then
      echo "up to date"
      exit 0
    fi
    echo "update available: sudo bash $0"
    exit 1
  fi

  if overlay_active && [ "$force" -eq 0 ]; then
    cat >&2 <<EOF
error: the overlay filesystem is on - /opt and /etc are RAM-backed right now, so an update
would be gone at the next reboot. Do it in three steps:
  1. sudo raspi-config nonint disable_overlayfs && sudo reboot
  2. sudo bash $SRC/deploy/update.sh
  3. sudo raspi-config nonint enable_overlayfs && sudo reboot
(--force runs the update anyway, for a test that may be lost.)
EOF
    exit 1
  fi

  if [ -n "$tag" ]; then
    target="$tag"
  else
    target="$(latest_tag)" || die "could not fetch the latest release from GitHub" 2
  fi

  echo "==> fetching tags"
  git -C "$SRC" fetch --tags --prune --quiet origin
  git -C "$SRC" rev-parse -q --verify "refs/tags/$target^{commit}" >/dev/null \
    || die "no such release tag: $target"

  if [ "$current" = "$target" ] && [ "$force" -eq 0 ]; then
    echo "already on $target, nothing to do (--force reinstalls anyway)"
    exit 0
  fi

  echo "==> checking out $target (was $current)"
  git -C "$SRC" checkout --force --detach --quiet "$target"

  echo "==> installing the package into $VENV"
  "$VENV/bin/pip" install --upgrade "$SRC[pi]"

  echo "==> systemd units"
  local unit
  for unit in "$SRC"/deploy/*.service "$SRC"/deploy/*.timer "$SRC"/deploy/*.path; do
    install -m 0644 "$unit" /etc/systemd/system/
  done
  systemctl daemon-reload
  systemctl enable studylife-display-restore.service studylife-display-persist.path \
    studylife-display-credentials.path studylife-display.timer \
    studylife-display-web.service >/dev/null 2>&1 || true
  systemctl start studylife-display-credentials.path >/dev/null 2>&1 || true

  echo "==> restarting the web interface and refreshing the panel once"
  systemctl restart studylife-display-web.service
  systemctl start studylife-display.service \
    || echo "    note: the refresh failed, see journalctl -u studylife-display.service -n 30"

  echo "updated to $target: $("$VENV/bin/studylife-display" --version)"
}

# Everything above is parsed before this line runs, so the checkout replacing this very
# file halfway through does not change what bash executes.
main "$@"
