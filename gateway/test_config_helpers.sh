#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export HOMEAI_CONFIG_DIR="$TMP/config"
export HOMEAI_CONFIG_FILE="$HOMEAI_CONFIG_DIR/gateway.env"
source ./homeai_env.sh
mkdir -p "$HOMEAI_CONFIG_DIR"
cat > "$HOMEAI_CONFIG_FILE" <<'CFG'
KEEP_ME=yes
OPENCLAW_SSH_HOST=old-host
OPENCLAW_SSH_HOST=duplicate-old-host
CFG
homeai_upsert_config_value OPENCLAW_SSH_HOST new-host
homeai_upsert_config_value OPENCLAW_SSH_USER test-user
[[ "$(homeai_config_get KEEP_ME)" == "yes" ]]
[[ "$(homeai_config_get OPENCLAW_SSH_HOST)" == "new-host" ]]
[[ "$(homeai_config_get OPENCLAW_SSH_USER)" == "test-user" ]]
[[ "$(grep -c '^OPENCLAW_SSH_HOST=' "$HOMEAI_CONFIG_FILE")" == "1" ]]
echo "HomeAIAgent config helper regression: PASS"
