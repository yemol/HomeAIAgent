#!/bin/zsh
set -euo pipefail

export HOMEAI_CONFIG_DIR="${HOMEAI_CONFIG_DIR:-$HOME/.config/HomeAIAgent}"
export HOMEAI_CONFIG_FILE="${HOMEAI_CONFIG_FILE:-$HOMEAI_CONFIG_DIR/gateway.env}"

homeai_migrate_legacy_env() {
  local legacy="${1:-./.env}"
  if [ -f "$HOMEAI_CONFIG_FILE" ]; then
    return 0
  fi
  if [ -f "$legacy" ]; then
    mkdir -p "$HOMEAI_CONFIG_DIR"
    cp "$legacy" "$HOMEAI_CONFIG_FILE"
    chmod 600 "$HOMEAI_CONFIG_FILE"
    echo "[CONFIG] migrated $legacy -> $HOMEAI_CONFIG_FILE"
  fi
}

homeai_require_config() {
  if [ ! -f "$HOMEAI_CONFIG_FILE" ]; then
    echo "[FAIL] Persistent config missing:"
    echo "       $HOMEAI_CONFIG_FILE"
    echo "Run ./setup_mac.sh"
    exit 2
  fi
}

homeai_source_config() {
  homeai_require_config
  set -a
  source "$HOMEAI_CONFIG_FILE"
  set +a
}


homeai_config_get() {
  local key="$1"
  local cfg="${2:-$HOMEAI_CONFIG_FILE}"
  [ -f "$cfg" ] || return 0
  grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$cfg" 2>/dev/null \
    | tail -n 1 | cut -d= -f2- || true
}

homeai_upsert_config_value() {
  local key="$1"
  local value="$2"
  local cfg="${3:-$HOMEAI_CONFIG_FILE}"
  local tmp="${cfg}.tmp.$$"
  mkdir -p "$(dirname "$cfg")"
  [ -f "$cfg" ] || : > "$cfg"
  awk -v key="$key" -v value="$value" '
    BEGIN { done = 0 }
    $0 ~ "^[[:space:]]*(export[[:space:]]+)?" key "=" {
      if (!done) {
        print key "=" value
        done = 1
      }
      next
    }
    { print }
    END {
      if (!done) print key "=" value
    }
  ' "$cfg" > "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$cfg"
}


homeai_missing_required_config() {
  local missing=()
  local key value
  for key in \
    VOLCENGINE_API_KEY \
    OPENCLAW_TOKEN \
    OPENCLAW_SSH_USER \
    OPENCLAW_SSH_HOST
  do
    value="$(homeai_config_get "$key")"
    if [ -z "$value" ]; then
      missing+=("$key")
    fi
  done
  if [ ${#missing[@]} -gt 0 ]; then
    printf '%s\n' "${missing[@]}"
    return 1
  fi
  return 0
}

homeai_require_complete_config() {
  local missing
  missing="$(homeai_missing_required_config || true)"
  if [ -n "$missing" ]; then
    echo "[FAIL] Persistent config is incomplete: $HOMEAI_CONFIG_FILE"
    echo "$missing" | while IFS= read -r key; do
      [ -n "$key" ] && echo "       missing: $key"
    done
    echo "Run ./configure_mac.sh once to repair the persistent config."
    echo "The values are written to $HOMEAI_CONFIG_FILE and survive reboot/project replacement."
    exit 2
  fi
}

homeai_enforce_standard_voice() {
  # Standard-voice convergence guard.
  # Do not rely on a one-time marker: every Gateway start verifies the persisted
  # values and rewrites only the TTS resource/voice fields when they drift.
  local desired_voice="zh_female_vv_uranus_bigtts"
  local desired_resource="seed-tts-2.0"
  local cfg="$HOMEAI_CONFIG_FILE"

  [ -f "$cfg" ] || return 0

  mkdir -p "$HOMEAI_CONFIG_DIR"
  local current_voice=""
  local current_resource=""
  current_voice="$(grep '^VOLCENGINE_TTS_VOICE=' "$cfg" 2>/dev/null | tail -n 1 | cut -d= -f2- || true)"
  current_resource="$(grep '^VOLCENGINE_TTS_RESOURCE_ID=' "$cfg" 2>/dev/null | tail -n 1 | cut -d= -f2- || true)"

  if [ "$current_voice" != "$desired_voice" ] || [ "$current_resource" != "$desired_resource" ]; then
    local stamp tmp backup
    stamp="$(date +%Y%m%d_%H%M%S)"
    backup="$cfg.bak_standard_voice_$stamp"
    tmp="$cfg.tmp_standard_voice_$$"
    cp "$cfg" "$backup"
    chmod 600 "$backup"

    awk -v voice="$desired_voice" -v resource="$desired_resource" '
      BEGIN { voice_done = 0; resource_done = 0 }
      /^[[:space:]]*(export[[:space:]]+)?VOLCENGINE_TTS_VOICE=/ {
        if (!voice_done) {
          print "VOLCENGINE_TTS_VOICE=" voice
          voice_done = 1
        }
        next
      }
      /^[[:space:]]*(export[[:space:]]+)?VOLCENGINE_TTS_RESOURCE_ID=/ {
        if (!resource_done) {
          print "VOLCENGINE_TTS_RESOURCE_ID=" resource
          resource_done = 1
        }
        next
      }
      { print }
      END {
        if (!resource_done) print "VOLCENGINE_TTS_RESOURCE_ID=" resource
        if (!voice_done) print "VOLCENGINE_TTS_VOICE=" voice
      }
    ' "$cfg" > "$tmp"
    chmod 600 "$tmp"
    mv "$tmp" "$cfg"
    echo "[CONFIG-ENFORCE] Volc TTS resource -> $desired_resource"
    echo "[CONFIG-ENFORCE] Volc TTS voice -> $desired_voice"
    echo "[CONFIG-ENFORCE] backup=$backup"
  else
    echo "[CONFIG-ENFORCE] standard Volc TTS pairing already correct"
  fi
}
