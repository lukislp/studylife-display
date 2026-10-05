#!/usr/bin/env bash
# Per-device setup that must not be baked into the SD-card image. Run as root by
# studylife-display-firstboot.service on every boot; every step is idempotent and does
# nothing once its result exists.
#
#   1. TLS certificate for DISPLAY_TLS=true, for <hostname>.local (the hostname chosen in
#      Raspberry Pi Imager is set by cloud-init before this runs)
#   2. import of the boot partition's studylife-display/setup.env (instance URL, web token,
#      panel, ...) into /etc/studylife-display.env; applied lines are commented out afterwards
#   3. a random web token when the environment file has none yet (never logged; written to
#      studylife-display/web-token.txt on the boot partition so it can be read without SSH)
#   4. the mDNS service file (it carries the instance id, which derives from /etc/machine-id)
#
# Paths are overridable for the tests (image/tests/firstboot-test.sh).
set -uo pipefail

ENV_FILE="${ENV_FILE:-/etc/studylife-display.env}"
BOOT_DIR="${BOOT_DIR:-/boot/firmware}"
SETUP_DIR="${SETUP_DIR:-$BOOT_DIR/studylife-display}"
STATE_DIR="${STATE_DIR:-/var/lib/studylife-display}"
TLS_CERT="${TLS_CERT:-/etc/studylife-display-tls.pem}"
TLS_KEY="${TLS_KEY:-/etc/studylife-display-tls.key}"
AVAHI_SCRIPT="${AVAHI_SCRIPT:-/opt/studylife-display/src/deploy/avahi-service.sh}"
MACHINE_ID_FILE="${MACHINE_ID_FILE:-/etc/machine-id}"

log() { echo "firstboot: $*"; }

# The account the services run as, read off the state directory the installer chowned.
service_group() {
  stat -c %G "$STATE_DIR" 2>/dev/null || echo root
}

issue_certificate() {
  if [ -f "$TLS_CERT" ] && [ -f "$TLS_KEY" ]; then
    return 0
  fi
  local host
  host="$(hostname)"
  log "generating the self-signed TLS certificate for $host.local"
  # Same parameters as deploy/install.sh: self-signed, 10 years, covers the mDNS name.
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3650 \
    -subj "/CN=$host.local" \
    -addext "subjectAltName=DNS:$host.local,DNS:$host" \
    -keyout "$TLS_KEY" -out "$TLS_CERT" 2>/dev/null || {
      log "openssl failed, no certificate"
      rm -f "$TLS_KEY" "$TLS_CERT"
      return 1
    }
  chown root:"$(service_group)" "$TLS_KEY"
  chmod 0640 "$TLS_KEY"
  chmod 0644 "$TLS_CERT"
}

# Replaces (or appends) KEY=value in the env file: a temp file in the same directory, then a
# rename, so a power cut cannot leave a truncated file. Values are validated before this.
set_env() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp "$(dirname "$ENV_FILE")/.studylife-env.XXXXXX")" || return 1
  awk -v k="$key" -v v="$value" '
    BEGIN { done = 0 }
    index($0, k "=") == 1 { if (!done) { print k "=" v; done = 1 } ; next }
    { print }
    END { if (!done) print k "=" v }
  ' "$ENV_FILE" > "$tmp" || { rm -f "$tmp"; return 1; }
  chown --reference="$ENV_FILE" "$tmp"
  chmod --reference="$ENV_FILE" "$tmp"
  mv -f "$tmp" "$ENV_FILE"
}

valid_value() {
  local key="$1" value="$2"
  case "$key" in
    STUDYLIFE_BASE_URL) [[ "$value" =~ ^https?://[A-Za-z0-9._:/@~+-]+$ ]] ;;
    DISPLAY_WEB_TOKEN) [[ "$value" =~ ^[A-Za-z0-9._:/@~+=-]{12,128}$ ]] ;;
    DISPLAY_PANEL) [[ "$value" =~ ^[a-z0-9_]{1,64}$ ]] ;;
    DISPLAY_ROTATE) [[ "$value" =~ ^(0|180)$ ]] ;;
    DISPLAY_LANGUAGE) [[ "$value" =~ ^(de|en)$ ]] ;;
    STUDYLIFE_TIMEZONE) [[ "$value" =~ ^[A-Za-z0-9_/+-]{1,64}$ ]] ;;
    *) return 2 ;;
  esac
}

token_in_env() {
  grep -Eq '^DISPLAY_WEB_TOKEN=.{12,}' "$ENV_FILE" 2>/dev/null
}

import_setup_env() {
  local file="$SETUP_DIR/setup.env" line key value rc tmp
  local applied=()
  [ -f "$file" ] || return 0
  [ -f "$ENV_FILE" ] || { log "no $ENV_FILE, skipping setup.env"; return 0; }
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in ''|'#'*|' '*'#'*) continue ;; esac
    key="${line%%=*}"
    value="${line#*=}"
    # Surrounding whitespace and one pair of quotes are forgiven (hand-edited on a PC).
    key="${key//[[:space:]]/}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    value="${value%\"}"; value="${value#\"}"
    value="${value%\'}"; value="${value#\'}"
    [ -n "$value" ] || continue
    valid_value "$key" "$value"
    rc=$?
    if [ "$rc" -eq 2 ]; then
      log "setup.env: ignoring unknown key $key"
      continue
    elif [ "$rc" -ne 0 ]; then
      log "setup.env: ignoring $key (value not accepted, see the comments in the file)"
      continue
    fi
    if set_env "$key" "$value"; then
      log "setup.env: applied $key"
      applied+=("$key")
    fi
  done < "$file"
  [ "${#applied[@]}" -gt 0 ] || return 0
  # Comment the applied lines out, so a token does not stay on the card. A new temp file
  # next to the original (the boot partition is vfat: no modes, no chown).
  tmp="$(mktemp "$SETUP_DIR/.setup.env.XXXXXX")" || return 0
  local keys
  keys="$(IFS='|'; echo "${applied[*]}")"
  awk -v keys="$keys" '
    BEGIN { n = split(keys, k, "|"); for (i = 1; i <= n; i++) want[k[i]] = 1 }
    {
      line = $0; sub(/\r$/, "", line)
      split(line, kv, "=")
      name = kv[1]; gsub(/[ \t]/, "", name)
      if (line !~ /^[ \t]*#/ && (name in want)) { print "# " name " was applied on a previous boot; line removed"; next }
      print
    }
  ' "$file" > "$tmp" && mv -f "$tmp" "$file" || rm -f "$tmp"
  # A token chosen here supersedes a generated one that is still lying on the card.
  case " ${applied[*]} " in
    *" DISPLAY_WEB_TOKEN "*) rm -f "$SETUP_DIR/web-token.txt" ;;
  esac
}

ensure_web_token() {
  [ -f "$ENV_FILE" ] || return 0
  token_in_env && return 0
  local token
  token="$(openssl rand -hex 16)" || { log "could not generate a web token"; return 1; }
  set_env DISPLAY_WEB_TOKEN "$token" || return 1
  # Never into the journal. The boot partition is where the person flashing the card can
  # read it without SSH; the file says to delete it afterwards.
  if [ -d "$SETUP_DIR" ] || mkdir -p "$SETUP_DIR" 2>/dev/null; then
    {
      echo "Web interface token of this display (http://<hostname>.local:8795/)."
      echo "Note it down, then delete this file: anyone holding the SD card can read it."
      echo "Choose your own with DISPLAY_WEB_TOKEN= in setup.env, or in /etc/studylife-display.env."
      echo
      echo "$token"
    } > "$SETUP_DIR/web-token.txt"
    log "no web token configured: generated one, written to $SETUP_DIR/web-token.txt"
  else
    log "no web token configured: generated one; read it with: sudo grep DISPLAY_WEB_TOKEN $ENV_FILE"
  fi
}

advertise() {
  [ -f "$AVAHI_SCRIPT" ] || return 0
  # Skip until systemd has initialised the machine id: deriving the instance id from an
  # empty or "uninitialized" one would give every card the same id.
  local id
  id="$(tr -d '[:space:]' < "$MACHINE_ID_FILE" 2>/dev/null)"
  case "$id" in
    ''|uninitialized) log "machine id not initialised yet, mDNS file not written"; return 0 ;;
  esac
  bash "$AVAHI_SCRIPT" || true
}

issue_certificate
import_setup_env
ensure_web_token
advertise
exit 0
