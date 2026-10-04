#!/usr/bin/env bash
# Writes the DNS-SD service file that lets Home Assistant's zeroconf find this display:
#
#   sudo bash deploy/avahi-service.sh
#
# The Pi already answers <hostname>.local through avahi; this adds one service of type
# _studylife-display._tcp, named "StudyLife Display (<hostname>)", on the web interface's
# port, with the TXT records
#
#   version=<package version>   tls=true|false   api=true|false   path=/   id=<instance id>
#
# read from /etc/studylife-display.env (DISPLAY_WEB_BIND, DISPLAY_TLS, DISPLAY_API_TOKEN -
# only whether the token is set, never its value) and from the installed package. `id` is
# the display's stable instance id (studylife_display.instance; left out when it cannot be
# computed). Both install.sh and update.sh run it, so the port, the TLS flag and the version stay current;
# the file is only rewritten when its content changes, avahi picks the change up by itself.
#
# It never fails the caller: without avahi's services directory it says so and exits 0, and
# so does any other problem - an update must not break because of a discovery nicety.
#
# Options (for tests and unusual layouts):
#   --dir DIR        avahi services directory   (default /etc/avahi/services)
#   --env-file FILE  the environment file       (default /etc/studylife-display.env)
#   --version V      the version to advertise   (default: asked of the installed package)
#   --id ID          the instance id to advertise (default: asked of the installed package)
set -uo pipefail

SERVICES_DIR=/etc/avahi/services
ENV_FILE=/etc/studylife-display.env
VENV=/opt/studylife-display/venv
VERSION=""
INSTANCE_ID=""
DEFAULT_STATE_PATH=/var/lib/studylife-display/last.json
SERVICE_FILE=studylife-display.service
DEFAULT_PORT=8795

env_value() {
  # The last `KEY=value` line of the env file, without surrounding quotes; empty when the
  # key or the file is missing. Parsed, not sourced: a token may hold any character.
  local line
  line="$(grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -n 1)" || true
  line="${line#"$1="}"
  line="${line%$'\r'}"
  line="${line%\"}"
  line="${line#\"}"
  line="${line%\'}"
  line="${line#\'}"
  printf '%s' "$line"
}

port_of() {
  # "0.0.0.0:8795" -> 8795, "[::]:8795" -> 8795, a bare "8795" -> 8795; anything that is
  # not a port number falls back to the default.
  local bind="${1:-}" port
  port="${bind##*:}"
  case "$port" in
    ''|*[!0-9]*) port="$DEFAULT_PORT" ;;
  esac
  printf '%s' "$port"
}

truthy() {
  # The values pydantic reads as true for DISPLAY_TLS.
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
    1|true|t|yes|y|on) return 0 ;;
    *) return 1 ;;
  esac
}

xml_escape() {
  printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

render_service() {
  local port="$1" version="$2" tls="$3" api="$4" id="${5:-}"
  cat <<EOF
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<!-- Written by deploy/avahi-service.sh on every install and update; do not edit. -->
<service-group>
  <name replace-wildcards="yes">StudyLife Display (%h)</name>
  <service>
    <type>_studylife-display._tcp</type>
    <port>$port</port>
    <txt-record>version=$(xml_escape "$version")</txt-record>
    <txt-record>tls=$tls</txt-record>
    <txt-record>api=$api</txt-record>
    <txt-record>path=/</txt-record>
$([ -n "$id" ] && printf '    <txt-record>id=%s</txt-record>' "$id")
  </service>
</service-group>
EOF
}

main() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --dir) SERVICES_DIR="${2:-}"; shift ;;
      --dir=*) SERVICES_DIR="${1#--dir=}" ;;
      --env-file) ENV_FILE="${2:-}"; shift ;;
      --env-file=*) ENV_FILE="${1#--env-file=}" ;;
      --version) VERSION="${2:-}"; shift ;;
      --version=*) VERSION="${1#--version=}" ;;
      --id) INSTANCE_ID="${2:-}"; shift ;;
      --id=*) INSTANCE_ID="${1#--id=}" ;;
      *) echo "    note: avahi-service.sh: ignoring unknown argument $1" ;;
    esac
    shift
  done

  if [ ! -d "$SERVICES_DIR" ]; then
    echo "    avahi services directory $SERVICES_DIR not found, skipping the mDNS advertisement"
    return 0
  fi

  if [ -z "$VERSION" ] && [ -x "$VENV/bin/python" ]; then
    VERSION="$("$VENV/bin/python" -c 'from studylife_display import package_version; print(package_version())' 2>/dev/null)" || VERSION=""
  fi
  VERSION="${VERSION:-0.0.0}"

  if [ -z "$INSTANCE_ID" ] && [ -x "$VENV/bin/python" ]; then
    local state_path
    state_path="$(env_value DISPLAY_STATE_PATH)"
    INSTANCE_ID="$("$VENV/bin/python" -c 'import sys; from pathlib import Path; from studylife_display.instance import instance_id; print(instance_id(Path(sys.argv[1]).parent))' "${state_path:-$DEFAULT_STATE_PATH}" 2>/dev/null)" || INSTANCE_ID=""
  fi
  case "$INSTANCE_ID" in
    ''|*[!0-9a-f]*) INSTANCE_ID="" ;;
  esac

  local port tls=false api=false tmp target="$SERVICES_DIR/$SERVICE_FILE"
  port="$(port_of "$(env_value DISPLAY_WEB_BIND)")"
  if truthy "$(env_value DISPLAY_TLS)"; then
    tls=true
  fi
  if [ -n "$(env_value DISPLAY_API_TOKEN)" ]; then
    api=true
  fi

  tmp="$(mktemp "$SERVICES_DIR/.studylife-display.XXXXXX" 2>/dev/null)" || {
    echo "    note: cannot write to $SERVICES_DIR, skipping the mDNS advertisement"
    return 0
  }
  render_service "$port" "$VERSION" "$tls" "$api" "$INSTANCE_ID" > "$tmp"
  if [ -f "$target" ] && cmp -s "$tmp" "$target"; then
    rm -f "$tmp"
    echo "    mDNS advertisement up to date (_studylife-display._tcp, port $port)"
    return 0
  fi
  chmod 0644 "$tmp"
  if mv -f "$tmp" "$target"; then
    echo "    mDNS advertisement written to $target (port $port, version $VERSION, tls=$tls, api=$api)"
  else
    rm -f "$tmp"
    echo "    note: could not write $target, skipping the mDNS advertisement"
  fi
  return 0
}

main "$@" || echo "    note: the mDNS advertisement could not be written"
exit 0
