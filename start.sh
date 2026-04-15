#!/usr/bin/env bash
# Starts LiteLLM, merging config.local.yaml into config.yaml if it exists.
# config.local.yaml is gitignored — use it for local/network-specific models (e.g. Ollama).

set -euo pipefail

BASE_CONFIG="config.yaml"
LOCAL_CONFIG="config.local.yaml"
MERGED_CONFIG="/tmp/claw-bedrock-merged.yaml"

if [ -f "$LOCAL_CONFIG" ]; then
  echo "[start.sh] Found $LOCAL_CONFIG — merging with $BASE_CONFIG..."
  pipenv run python3 - <<EOF
import yaml

with open("$BASE_CONFIG") as f:
    base = yaml.safe_load(f)

with open("$LOCAL_CONFIG") as f:
    local = yaml.safe_load(f)

base.setdefault("model_list", []).extend(local.get("model_list", []))

with open("$MERGED_CONFIG", "w") as f:
    yaml.dump(base, f, default_flow_style=False, allow_unicode=True)
EOF
  echo "[start.sh] Merged config written to $MERGED_CONFIG"
  CONFIG_TO_USE="$MERGED_CONFIG"
else
  echo "[start.sh] No $LOCAL_CONFIG found — using $BASE_CONFIG only"
  CONFIG_TO_USE="$BASE_CONFIG"
fi

exec pipenv run litellm --config "$CONFIG_TO_USE" --port 4000
